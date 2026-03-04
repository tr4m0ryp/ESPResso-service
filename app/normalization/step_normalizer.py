"""Journey step type normalizer: Avelero -> ESPResso vocabulary.

Supports both single-list normalization (backward compat) and bulk
normalization that deduplicates unknowns across an entire batch
before making a single NIM call.
"""

import logging

from app.normalization.cache import NormalizationCache
from app.normalization.nim_client import NIMClient

logger = logging.getLogger(__name__)

# Avelero stepType -> ESPResso preprocessing step vocabulary
_STEP_MAP: dict[str, str] = {
    # Direct mappings
    "cutting": "cutting",
    "sewing": "sewing",
    "dyeing": "dyeing",
    "bleaching": "bleaching",
    "finishing": "finishing",
    "printing": "printing",
    "weaving": "weaving",
    "knitting": "knitting",
    "spinning": "spinning",
    "washing": "washing",
    "coating": "coating",
    "laminating": "laminating",
    "assembly": "assembly",
    "pressing": "pressing",
    "felting": "felting",
    "braiding": "braiding",
    "extrusion": "extrusion",
    "moulding": "moulding",
    # Avelero variations -> canonical
    "dying": "dyeing",
    "stitching": "sewing",
    "screen printing": "printing",
    "digital printing": "printing",
    "heat pressing": "pressing",
    "embroidery": "sewing",
    "quilting": "sewing",
    "garment washing": "washing",
    "stone washing": "washing",
    "enzyme washing": "washing",
    "mercerizing": "mercerizing",
    "sanforizing": "sanforizing",
    "calendering": "calendering",
    "raising": "raising",
    "shearing": "shearing",
    "waterproofing": "waterproofing",
    "ginning": "ginning",
    "scouring": "scouring",
    "degumming": "degumming",
    "retting": "retting",
    "decortication": "decortication",
    "sorting": "sorting",
    "cleaning": "cleaning",
}

# ESPResso's full step vocabulary for NIM prompt and guided_choice
_VALID_STEPS = sorted(set(_STEP_MAP.values()))

# Grouped vocabulary for the system prompt
_STEP_GROUPS: dict[str, list[str]] = {
    "Pre-processing": [
        "ginning", "scouring", "degumming", "retting",
        "decortication", "sorting", "cleaning",
    ],
    "Primary processing": [
        "spinning", "weaving", "knitting", "braiding",
        "felting", "extrusion", "moulding",
    ],
    "Wet processing": [
        "dyeing", "bleaching", "mercerizing", "printing",
    ],
    "Finishing": [
        "finishing", "calendering", "raising", "shearing",
        "sanforizing", "coating", "laminating",
    ],
    "Construction": [
        "cutting", "sewing", "assembly", "pressing",
    ],
    "Special": [
        "waterproofing", "washing",
    ],
}


def _build_system_prompt() -> str:
    sections = []
    for group, steps in _STEP_GROUPS.items():
        items = ", ".join(steps)
        sections.append(f"  {group}: {items}")
    vocab_block = "\n".join(sections)

    return (
        "You are a textile manufacturing expert specializing in Life "
        "Cycle Assessment (LCA) for fashion products. Your task is to "
        "map raw manufacturing/processing step names to the canonical "
        "vocabulary used by carbon footprint prediction models.\n\n"
        "CANONICAL STEPS (grouped by processing category):\n"
        f"{vocab_block}\n\n"
        "RULES:\n"
        "- Output EXACTLY one canonical step name from the list above "
        "per item\n"
        "- If genuinely unrecognizable, output 'unknown'\n"
        "- Never invent new step names outside the vocabulary\n"
        "- Prefer the most specific match available\n\n"
        "EXAMPLES:\n"
        "- screen printing -> printing\n"
        "- garment dyeing -> dyeing\n"
        "- stone wash -> washing\n"
        "- embroidered -> sewing\n"
        "- heat seal -> pressing\n"
    )


_SYSTEM_PROMPT = _build_system_prompt()

# Choices for guided_choice: valid steps + "unknown"
_GUIDED_CHOICES = _VALID_STEPS + ["unknown"]


class StepNormalizer:
    """Normalize Avelero journey steps to ESPResso vocabulary."""

    def __init__(
        self, nim_client: NIMClient, cache: NormalizationCache,
    ):
        self._nim = nim_client
        self._cache = cache

    async def normalize(self, steps: list[str]) -> list[str]:
        """Normalize a list of step names (backward compat)."""
        results = []
        for step in steps:
            normalized = await self._normalize_single(step)
            if normalized and normalized != "unknown":
                results.append(normalized)
        return results

    async def _normalize_single(self, step: str) -> str:
        cleaned = step.strip().lower()

        if cleaned in _STEP_MAP:
            return _STEP_MAP[cleaned]

        cache_key = f"step:{cleaned}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            result = await self._nim.complete_with_choices(
                _SYSTEM_PROMPT,
                f"Step name: {cleaned}",
                _GUIDED_CHOICES,
            )
            result = result.strip().lower()
            await self._cache.set(cache_key, result)
            return result
        except Exception:
            logger.warning(
                "NIM step normalization failed for %r", cleaned,
            )
            return cleaned

    async def normalize_bulk(
        self, raw_steps: list[str],
    ) -> dict[str, str]:
        """Normalize a list of raw step names in bulk.

        1. Deduplicate input step names
        2. Filter out steps resolved by static map or cache
        3. Send remaining unknowns in one NIM call with guided_choice
        4. Cache all NIM results
        5. Return mapping: {raw_step: canonical_step}
           (excludes steps that resolved to "unknown")
        """
        result_map: dict[str, str] = {}
        unknowns: list[str] = []

        unique_steps = list(dict.fromkeys(raw_steps))

        for raw in unique_steps:
            cleaned = raw.strip().lower()

            if cleaned in _STEP_MAP:
                result_map[raw] = _STEP_MAP[cleaned]
                continue

            cache_key = f"step:{cleaned}"
            cached = await self._cache.get(cache_key)
            if cached is not None:
                result_map[raw] = cached
                continue

            unknowns.append(raw)

        if unknowns:
            try:
                cleaned_unknowns = [u.strip().lower() for u in unknowns]
                results = await self._nim.complete_bulk(
                    _SYSTEM_PROMPT,
                    cleaned_unknowns,
                    choices=_GUIDED_CHOICES,
                )
                for raw, canonical in zip(unknowns, results):
                    canonical = canonical.strip().lower()
                    if not canonical:
                        canonical = "unknown"
                    cache_key = f"step:{raw.strip().lower()}"
                    await self._cache.set(cache_key, canonical)
                    result_map[raw] = canonical
                    logger.info(
                        "NIM bulk normalized step: %r -> %r",
                        raw, canonical,
                    )
            except Exception:
                logger.warning(
                    "NIM bulk step normalization failed for %d steps, "
                    "falling back to per-item",
                    len(unknowns),
                )
                for raw in unknowns:
                    result = await self._normalize_single(raw)
                    result_map[raw] = result

        return result_map
