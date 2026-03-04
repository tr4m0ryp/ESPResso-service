"""Two-tier material name normalizer: static lookup then NIM fallback.

Supports both single-item normalization (backward compat) and bulk
normalization that deduplicates unknowns across an entire batch
before making a single NIM call.
"""

import logging

from app.normalization.cache import NormalizationCache
from app.normalization.nim_client import NIMClient
from app.normalization.synonym_map import SYNONYM_MAP, resolve_static

logger = logging.getLogger(__name__)

# Build canonical vocabulary list for guided_choice constraint
_CANONICAL_NAMES = sorted(set(SYNONYM_MAP.values()))

# Group materials by class for the LLM prompt
_MATERIAL_GROUPS: dict[str, list[str]] = {
    "Natural fibres": [],
    "Synthetic fibres": [],
    "Animal products": [],
    "Metals and hardware": [],
    "Textiles": [],
    "Yarns": [],
    "Other": [],
}

for _name in _CANONICAL_NAMES:
    _lower = _name.lower()
    if _lower.startswith("fibre, cotton") or _lower.startswith("fibre, flax") \
            or _lower.startswith("fibre, jute") or _lower.startswith("fibre, silk") \
            or "hemp" in _lower or "coconut" in _lower or "cellulose" in _lower:
        _MATERIAL_GROUPS["Natural fibres"].append(_name)
    elif _lower.startswith("fibre, polyester") or _lower.startswith("fibre, viscose") \
            or "nylon" in _lower or "polyurethane" in _lower or "polypropylene" in _lower \
            or "polylactic" in _lower or "polyethylene" in _lower \
            or "ethylene vinyl" in _lower or "synthetic rubber" in _lower:
        _MATERIAL_GROUPS["Synthetic fibres"].append(_name)
    elif "feather" in _lower or "wool" in _lower or "fleece" in _lower \
            or "hide" in _lower or "leather" in _lower:
        _MATERIAL_GROUPS["Animal products"].append(_name)
    elif "steel" in _lower or "aluminium" in _lower or "zinc" in _lower \
            or "seal" in _lower:
        _MATERIAL_GROUPS["Metals and hardware"].append(_name)
    elif _lower.startswith("textile"):
        _MATERIAL_GROUPS["Textiles"].append(_name)
    elif _lower.startswith("yarn"):
        _MATERIAL_GROUPS["Yarns"].append(_name)
    else:
        _MATERIAL_GROUPS["Other"].append(_name)


def _build_system_prompt() -> str:
    sections = []
    for group, names in _MATERIAL_GROUPS.items():
        if names:
            items = "\n".join(f"  - {n}" for n in names)
            sections.append(f"{group}:\n{items}")
    vocab_block = "\n".join(sections)

    return (
        "You are a materials science expert specializing in Life Cycle "
        "Assessment (LCA) for textile and fashion products. Your task is "
        "to map raw material names to the canonical vocabulary used by "
        "carbon footprint prediction models.\n\n"
        "CANONICAL VOCABULARY (grouped by class):\n"
        f"{vocab_block}\n\n"
        "RULES:\n"
        "- Output EXACTLY one canonical name from the list above per item\n"
        "- Never invent new names outside the vocabulary\n"
        "- Prefer the most specific match available\n"
        "- When in doubt between recycled and conventional variants, "
        "use the conventional variant\n"
        "- Strip annotations like '(not listed, assumed...)' from input "
        "before matching\n"
        "- 'Lyocell', 'Tencel', 'modal', 'rayon', 'bamboo' all map to "
        "'fibre, viscose'\n"
        "- 'Elastane', 'spandex', 'Lycra' all map to "
        "'polyurethane, flexible foam'\n\n"
        "EXAMPLES:\n"
        "- organic cotton jersey -> fibre, cotton, organic\n"
        "- recycled PET yarn -> fibre, polyester\n"
        "- goat leather -> sheep fleece in the grease\n"
        "- Tencel lyocell -> fibre, viscose\n"
        "- stainless steel buckle -> steel, chromium steel 18/8\n"
    )


_SYSTEM_PROMPT = _build_system_prompt()


class MaterialNormalizer:
    """Normalize raw material names to ESPResso canonical vocabulary."""

    def __init__(
        self, nim_client: NIMClient, cache: NormalizationCache,
    ):
        self._nim = nim_client
        self._cache = cache

    async def normalize(self, raw_name: str) -> str:
        """Normalize a single material name (backward compat).

        1. Lowercase + strip
        2. Check static SYNONYM_MAP
        3. Check cache
        4. Call NIM with guided_choice
        5. Cache the result
        """
        if not raw_name:
            return raw_name

        cleaned = raw_name.strip().lower()

        static = resolve_static(raw_name.strip())
        if static is not None:
            return static
        static = resolve_static(cleaned)
        if static is not None:
            return static

        cache_key = f"material:{cleaned}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            result = await self._nim.complete_with_choices(
                _SYSTEM_PROMPT,
                f"Material name: {cleaned}",
                _CANONICAL_NAMES,
            )
            result = result.strip()
            if result:
                await self._cache.set(cache_key, result)
                logger.info(
                    "NIM normalized material: %r -> %r", cleaned, result,
                )
                return result
        except Exception:
            logger.warning(
                "NIM normalization failed for %r, using original",
                cleaned,
            )

        return cleaned

    async def normalize_bulk(
        self, raw_names: list[str],
    ) -> dict[str, str]:
        """Normalize a list of raw material names in bulk.

        1. Deduplicate input names
        2. Filter out names resolved by static map or cache
        3. Send remaining unknowns in one NIM call with guided_choice
        4. Cache all NIM results
        5. Return full mapping: {raw_name: canonical_name}
        """
        result_map: dict[str, str] = {}
        unknowns: list[str] = []

        unique_names = list(dict.fromkeys(raw_names))

        for raw in unique_names:
            cleaned = raw.strip().lower()

            static = resolve_static(raw.strip())
            if static is None:
                static = resolve_static(cleaned)
            if static is not None:
                result_map[raw] = static
                continue

            cache_key = f"material:{cleaned}"
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
                    choices=_CANONICAL_NAMES,
                )
                for raw, canonical in zip(unknowns, results):
                    canonical = canonical.strip()
                    if not canonical:
                        canonical = raw.strip().lower()
                    cache_key = f"material:{raw.strip().lower()}"
                    await self._cache.set(cache_key, canonical)
                    result_map[raw] = canonical
                    logger.info(
                        "NIM bulk normalized material: %r -> %r",
                        raw, canonical,
                    )
            except Exception:
                logger.warning(
                    "NIM bulk normalization failed for %d materials, "
                    "falling back to per-item",
                    len(unknowns),
                )
                for raw in unknowns:
                    result_map[raw] = await self.normalize(raw)

        return result_map
