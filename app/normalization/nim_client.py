"""Async NVIDIA NIM client with key rotation and rate-limit cooldowns."""

import asyncio
import logging

import httpx

from app.config import Settings
from app.normalization.nim_key_pool import NIMKeyPool

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY = 2.0


class NIMClient:
    """Async NVIDIA NIM chat completions client."""

    def __init__(self, settings: Settings):
        self._model_id = settings.NIM_MODEL_ID
        self._base_url = settings.NIM_BASE_URL.rstrip("/")
        self._semaphore = asyncio.Semaphore(settings.NIM_CONCURRENCY_LIMIT)
        self._pool = NIMKeyPool(settings.nim_api_key_list)

    async def complete(
        self, system_prompt: str, user_prompt: str,
    ) -> str:
        """Send a chat completion request to NIM.

        Returns the assistant's response text. Retries on failure.
        """
        async with self._semaphore:
            return await self._request(system_prompt, user_prompt)

    async def complete_with_choices(
        self,
        system_prompt: str,
        user_prompt: str,
        choices: list[str],
    ) -> str:
        """Send a chat completion constrained to one of the given choices.

        Uses guided_choice to guarantee the response is exactly one of
        the provided values -- no hallucination possible.
        """
        extra_body = {"guided_choice": choices}
        async with self._semaphore:
            return await self._request(
                system_prompt, user_prompt, extra_body=extra_body,
            )

    async def complete_bulk(
        self,
        system_prompt: str,
        items: list[str],
        choices: list[str] | None = None,
    ) -> list[str]:
        """Normalize multiple items in a single NIM call.

        Sends all items as a numbered list and expects one canonical
        name per line in the same order. If choices is provided, each
        result is validated against the allowed values; mismatches are
        re-queried individually with guided_choice.
        """
        if not items:
            return []

        numbered = "\n".join(
            f"{i + 1}. {item}" for i, item in enumerate(items)
        )
        user_prompt = (
            "Normalize each item below. Output one canonical name per "
            "line, in the same order. No numbering, no explanation.\n\n"
            + numbered
        )
        max_tokens = max(256, len(items) * 60)

        async with self._semaphore:
            raw = await self._request(
                system_prompt, user_prompt, max_tokens=max_tokens,
            )

        lines = [ln.strip().strip('"').strip("'") for ln in raw.splitlines() if ln.strip()]

        # Strip leading numbering (e.g. "1. fibre, cotton" -> "fibre, cotton")
        cleaned: list[str] = []
        for line in lines:
            stripped = line.lstrip("0123456789.)-: ")
            cleaned.append(stripped if stripped else line)
        lines = cleaned

        # Pad or truncate to match input length
        while len(lines) < len(items):
            lines.append("")
        lines = lines[: len(items)]

        # Validate against choices if provided; re-query failures
        if choices:
            choice_set = set(choices)
            for i, result in enumerate(lines):
                if result not in choice_set:
                    try:
                        corrected = await self.complete_with_choices(
                            system_prompt,
                            f"Normalize: {items[i]}",
                            choices,
                        )
                        lines[i] = corrected.strip()
                    except Exception:
                        logger.warning(
                            "Guided re-query failed for %r, keeping "
                            "bulk result %r",
                            items[i], result,
                        )

        return lines

    async def _request(
        self,
        system_prompt: str,
        user_prompt: str,
        extra_body: dict | None = None,
        max_tokens: int = 256,
    ) -> str:
        """Send a chat completion request with key rotation on 429."""
        url = f"{self._base_url}/chat/completions"
        payload: dict = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        if extra_body:
            payload.update(extra_body)

        for attempt in range(_MAX_RETRIES):
            key_idx, client = await self._pool.get_client()
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429:
                    self._pool.mark_rate_limited(key_idx)
                    if self._pool.key_count > 1:
                        # Rotate immediately without sleeping
                        logger.warning(
                            "NIM 429 on key %d (attempt %d/%d), "
                            "rotating to next key",
                            key_idx, attempt + 1, _MAX_RETRIES,
                        )
                        continue
                    # Single key -- pool.get_client will wait for cooldown
                    logger.warning(
                        "NIM 429 on single key (attempt %d/%d), "
                        "waiting for cooldown",
                        attempt + 1, _MAX_RETRIES,
                    )
                    continue

                if attempt < _MAX_RETRIES - 1:
                    logger.warning(
                        "NIM request failed (attempt %d/%d, status %d), "
                        "retrying in %.1fs",
                        attempt + 1, _MAX_RETRIES, status, _RETRY_DELAY,
                    )
                    await asyncio.sleep(_RETRY_DELAY)
                else:
                    logger.error(
                        "NIM request failed after %d attempts: %s",
                        _MAX_RETRIES, exc,
                    )
                    raise

            except (httpx.RequestError, KeyError, IndexError) as exc:
                if attempt < _MAX_RETRIES - 1:
                    logger.warning(
                        "NIM request error (attempt %d/%d): %s, "
                        "retrying in %.1fs",
                        attempt + 1, _MAX_RETRIES, exc, _RETRY_DELAY,
                    )
                    await asyncio.sleep(_RETRY_DELAY)
                else:
                    logger.error(
                        "NIM request failed after %d attempts: %s",
                        _MAX_RETRIES, exc,
                    )
                    raise

        return ""

    async def health_check(self) -> bool:
        """Check if NIM is reachable."""
        try:
            result = await self.complete(
                "You are a test assistant.",
                "Reply with OK.",
            )
            return len(result.strip()) > 0
        except Exception:
            return False

    async def close(self) -> None:
        await self._pool.close_all()
