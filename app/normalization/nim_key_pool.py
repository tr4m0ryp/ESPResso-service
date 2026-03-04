"""NIM API key pool with round-robin rotation and per-key cooldown."""

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

_COOLDOWN_SECONDS = 60.0
_TIMEOUT = 90.0


class NIMKeyPool:
    """Manages multiple NIM API keys with per-key rate-limit cooldowns.

    Keys are selected via round-robin. When a key hits a 429 rate limit,
    it enters a 60-second cooldown and the pool rotates to the next
    available key. If all keys are in cooldown, the pool waits for the
    soonest one to recover.
    """

    def __init__(self, api_keys: list[str]) -> None:
        if not api_keys:
            raise ValueError("At least one NIM API key is required")
        self._keys = api_keys
        self._clients: dict[int, httpx.AsyncClient] = {}
        self._cooldowns: dict[int, float] = {}
        self._next_index = 0
        self._lock = asyncio.Lock()
        logger.info("NIM key pool initialized with %d key(s)", len(api_keys))

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def _build_client(self, key_index: int) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self._keys[key_index]}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(_TIMEOUT),
        )

    def _is_cooled_down(self, key_index: int) -> bool:
        expiry = self._cooldowns.get(key_index)
        if expiry is None:
            return False
        if time.monotonic() >= expiry:
            del self._cooldowns[key_index]
            return False
        return True

    async def get_client(self) -> tuple[int, httpx.AsyncClient]:
        """Return the next available (key_index, client) pair.

        Skips keys that are in cooldown. If all keys are in cooldown,
        waits for the soonest one to recover.
        """
        async with self._lock:
            # Try each key once starting from _next_index
            for _ in range(len(self._keys)):
                idx = self._next_index % len(self._keys)
                self._next_index = idx + 1
                if not self._is_cooled_down(idx):
                    return idx, self._get_or_create_client(idx)

            # All keys in cooldown -- find soonest expiry
            soonest_idx, soonest_expiry = min(
                self._cooldowns.items(), key=lambda kv: kv[1],
            )
            wait_time = max(0.0, soonest_expiry - time.monotonic())
            logger.warning(
                "All %d NIM keys in cooldown, waiting %.1fs for key %d",
                len(self._keys), wait_time, soonest_idx,
            )

        # Sleep outside the lock so other coroutines aren't blocked
        if wait_time > 0:
            await asyncio.sleep(wait_time)

        async with self._lock:
            self._cooldowns.pop(soonest_idx, None)
            self._next_index = soonest_idx + 1
            return soonest_idx, self._get_or_create_client(soonest_idx)

    def _get_or_create_client(self, key_index: int) -> httpx.AsyncClient:
        client = self._clients.get(key_index)
        if client is None or client.is_closed:
            client = self._build_client(key_index)
            self._clients[key_index] = client
        return client

    def mark_rate_limited(self, key_index: int) -> None:
        """Put a key into cooldown for 60 seconds."""
        expiry = time.monotonic() + _COOLDOWN_SECONDS
        self._cooldowns[key_index] = expiry
        logger.warning(
            "NIM key %d rate-limited, cooldown until %.1f "
            "(%d/%d keys available)",
            key_index, expiry,
            len(self._keys) - len(self._cooldowns), len(self._keys),
        )

    async def close_all(self) -> None:
        """Shut down all httpx clients."""
        for idx, client in self._clients.items():
            if not client.is_closed:
                await client.aclose()
        self._clients.clear()
        self._cooldowns.clear()
        logger.info("NIM key pool closed")
