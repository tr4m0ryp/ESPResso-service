"""Static synonym map for material name resolution.

Ported from ESPResso layer_6/core/_synonym_data.py and
layer_6/core/material_aliases.py.
"""

import re
from typing import Optional

# Shorthand aliases for long reference names
_CK = "chicken feathers, at slaughterhouse"
_DK = "duck feathers, at slaughterhouse"
_PU = "polyurethane, flexible foam"
_WC = "wool, conventional, at farm gate"
_WO = "wool, organic, at farm gate"
_WO2 = "wool, organic (system 2), at farm gate"
_WR = "wool, Roquefort dairy sheep, at farm gate"
_TWC = "textile, woven cotton"
_FV = "fibre, viscose"
_FP = "fibre, polyester"
_SC18 = "steel, chromium steel 18/8"
_PET = "polyethylene terephthalate, granulate, amorphous"
_PUFR = "polyurethane, flexible foam, flame retardant"
_EVA = "ethylene vinyl acetate copolymer"
_CFS = "coconut fibre, at storehouse"
_HFRA = "hemp fibre, raw, at farm gate"
_CFH = "cottonized fibre, hemp"
_BH = "beef hides, at slaughterhouse (GB)"
_CH = "cowhide, from beef, at slaughterhouse"

SYNONYM_MAP: dict[str, str] = {
    "canopy: fibre, cotton": "fibre, cotton",
    "canopy: fibre, cotton, organic": "fibre, cotton, organic",
    "canopy: fibre, polyester": _FP,
    "canopy: fibre, viscose": _FV,
    "frame: aluminium, primary, ingot": "aluminium, primary, ingot",
    "frame: aluminium, wrought alloy": "aluminium, wrought alloy",
    "handle: zinc": "zinc",
    "ribs: fibre, jute": "fibre, jute",
    "fabric: textile, kenaf": "textile, kenaf",
    "fabric: textile, knit cotton": "textile, knit cotton",
    "fabric: textile, woven cotton": _TWC,
    "sunbrella: fibre, polyester": _FP,
    "water-resistant: ethylene vinyl acetate copolymer": _EVA,
    "waterproof: polyurethane, flexible foam": _PU,
    "waterproof: polyurethane, flexible foam, flame retardant": _PUFR,
    "acrylic": _FP,
    "cotton": "fibre, cotton",
    "cotton, organic": "fibre, cotton, organic",
    "elastane": _PU,
    "elastane (not listed, assumed 2% for stretch)": _PU,
    "hemp": _HFRA,
    "linen": "fibre, flax",
    "lycra": _PU,
    "lyocell": _FV,
    "microfibre": _FP,
    "nylon": "nylon 6",
    "organic cotton": "fibre, cotton, organic",
    "polyamide": "nylon 6",
    "polyamide 6": "nylon 6",
    "polyamide 6-6": "nylon 6-6",
    "polycarbonate": "polypropylene, granulate",
    "polyester": _FP,
    "polyester (not listed, assumed 20% for durability)": _FP,
    "polyester fibre": _FP,
    "polyester, granulate": _PET,
    "polyester, granulate, amorphous": _PET,
    "polyester, recycled": _FP,
    "polypropylene": "polypropylene, granulate",
    "polyurethane, flame retardant": _PUFR,
    "recycled nylon": "nylon 6",
    "recycled polyester": _FP,
    "rubber, synthetic": "synthetic rubber",
    "silk": "textile, silk",
    "silk yarn": "yarn, silk",
    "silk, short": "fibre, silk, short",
    "silver-plated steel": _SC18,
    "spandex": _PU,
    "stainless steel, 18/8": _SC18,
    "stainless steel, chromium steel 18/8": _SC18,
    "stainless steel, low-alloyed": "steel, low-alloyed",
    "tencel": _FV,
    "titanium": "aluminium, wrought alloy",
    "titanium alloy": "aluminium, wrought alloy",
    "viscose": _FV,
    "bamboo fibre": _FV,
    "bamboo viscose": _FV,
    "chicken feathers": _CK,
    "down feathers (chicken)": _CK,
    "down feathers (duck)": _DK,
    "down feathers (goose)": _DK,
    "down feathers (not listed, assuming chicken feathers)": _CK,
    "down insulation (assumed from chicken feathers)": _CK,
    "down insulation (assumed from duck feathers)": _DK,
    "down insulation (assumed from duck feathers, fattened)":
        "duck feathers (fattened), at slaughterhouse",
    "down insulation (chicken feathers, at slaughterhouse)": _CK,
    "down insulation (duck feathers, at slaughterhouse)": _DK,
    "down insulation (implicit in subcategory)": _CK,
    "down insulation (not listed, assumed from chicken feathers)": _CK,
    "down insulation (not listed, assumed)": _CK,
    "down, chicken feathers, at slaughterhouse": _CK,
    "down, duck feathers": _DK,
    "down, duck feathers, at slaughterhouse": _DK,
    "duck feathers": "duck feathers, at slaughterhouse",
    "elastic (nylon 6-6)": "nylon 6-6",
    "fibre, Lycra": _PU,
    "fibre, elastane": _PU,
    "fibre, elastane (assumed polyurethane)": _PU,
    "fibre, elastane (not listed, assumed from polyurethane)": _PU,
    "fibre, elastane (not listed, assumed polyurethane)": _PU,
    "fibre, elastane (not listed, assumed)": _PU,
    "fibre, elasthan (assumed nylon 6)": "nylon 6",
    "fibre, elasthan (not listed, assumed nylon 6)": "nylon 6",
    "fibre, elasthan (not listed, assumed similar to polyurethane)": _PU,
    "fibre, elasthan (polyurethane)": _PU,
    "fibre, lycra": _PU,
    "fibre, spandex": _PU,
    "fibre, spandex (not listed, skipping, using fibre, polyester)": _FP,
    "fibre, TENCEL": _FV,
    "fibre, Tencel": _FV,
    "fibre, Tencel (lyocell)": _FV,
    "fibre, elyocell (not listed, substituting with fibre, viscose)": _FV,
    "fibre, lyocell": _FV,
    "fibre, lyocell (assumed viscose)": _FV,
    "fibre, modal": _FV,
    "fibre, rayon": _FV,
    "fibre, tencel": _FV,
    "fibre, tencel (assumed viscose)": _FV,
    "fibre, cotton, recycled": "fibre, cotton",
    "fibre, organic cotton": "fibre, cotton, organic",
    "fibre, recycled cotton": "fibre, cotton",
    "fibre, nylon": "nylon 6",
    "fibre, nylon 6": "nylon 6",
    "fibre, nylon 6-6": "nylon 6-6",
    "fibre, polyamide": "nylon 6",
    "fibre, recycled nylon": "nylon 6",
    "fibre, recycled polyester": _FP,
    "fibre, polyester, recycled": _FP,
    "fibre, wool": _WC,
    "fibre, wool, Roquefort dairy sheep": _WR,
    "fibre, wool, conventional": _WC,
    "fibre, wool, conventional, at farm gate": _WC,
    "fibre, wool, organic": _WO,
    "fibre, wool, organic (system 2)": _WO2,
    "fibre, wool, organic (system 2), at farm gate": _WO2,
    "fibre, wool, organic, at farm gate": _WO,
    "fibre, merino wool": _WC,
    "fibre, mohair": "sheep fleece in the grease",
    "fibre, organic wool": _WO,
    "fibre, organic, wool": _WO,
    "fibre, organic, wool (system 2)": _WO2,
    "fibre, organic, wool, system 2": _WO2,
    "wool, conventional": _WC,
    "wool, organic": _WO,
    "wood, Roquefort dairy sheep": _WR,
    "wood, conventional, at farm gate": _WC,
    "fibre, cotton, conventional": "fibre, cotton",
    "fibre, wool, Roquefort dairy sheep, at farm gate": _WR,
    "fibre, organic": "fibre, cotton, organic",
    "fibre, organic (system 2)": _WO2,
    "fibre, organic (system 2), at farm gate": _WO2,
    "fibre, organic, at farm gate": _WO,
    "fibre, linen": "fibre, flax",
    "fibre, hemp": _HFRA,
    "fibre, hemp (cottonized)": _CFH,
    "fibre, cottonized, hemp": _CFH,
    "hemp fibre": _HFRA,
    "hemp fibre, raw": _HFRA,
    "fibre, coconut": _CFS,
    "fibre, coconut fibre": _CFS,
    "fibre, coconut fibre, at storehouse": _CFS,
    "fibre, coconut, at storehouse": _CFS,
    "fibre, silk": "fibre, silk, short",
    "fibre, chiffon": "textile, silk",
    "fibre, acetate": "cellulose fibre",
    "fibre, acrylic": _FP,
    "fibre, bamboo": _FV,
    "fibre, cashmere": "sheep fleece in the grease",
    "fibre, cellulose": "cellulose fibre",
    "fibre, corn": "polylactic acid, granulate",
    "fibre, metallic": _SC18,
    "fibre, milk": "cellulose fibre",
    "fibre, soy": "cellulose fibre",
    "leather (not listed, assumed similar to textile, woven cotton)": _TWC,
    "leather, beef hides": _BH,
    "leather, beef hides, at slaughterhouse (GB)": _BH,
    "leather, cowhide": _CH,
    "leather, cowhide, from beef, at slaughterhouse": _CH,
    "leather, lamb hide": "lamb hide, at slaughterhouse",
    "leather, lamb hide, at slaughterhouse": "lamb hide, at slaughterhouse",
    "leather, sheep fleece in the grease": "sheep fleece in the grease",
    "leather, veal hide": "veal hide, at slaughterhouse",
    "leather, veal hide, at slaughterhouse": "veal hide, at slaughterhouse",
    "leather, wool, conventional, at farm gate": _WC,
    "natural rubber": "seal, natural rubber based",
    "natural rubber based": "seal, natural rubber based",
    "polyethylene terephthalate": _PET,
    "polyethylene terephthalate, granulate": _PET,
    "polylactic acid": "polylactic acid, granulate",
    "textile, cotton": _TWC,
    "textile, cotton blend": _TWC,
    "textile, cotton sateen": _TWC,
    "textile, cotton twill": _TWC,
    "textile, cotton voile": _TWC,
    "textile, denim": _TWC,
    "textile, denim cotton": _TWC,
    "textile, indigo denim": _TWC,
    "textile, lightweight denim": _TWC,
    "textile, stretch denim": _TWC,
    "textile, organic denim": _TWC,
    "textile, recycled denim": _TWC,
    "textile, vintage denim": _TWC,
    "textile, knitted cotton": "textile, knit cotton",
    "textile, organic cotton": _TWC,
    "textile, organic cotton twill": _TWC,
    "textile, recycled cotton": _TWC,
    "textile, oxford cloth": _TWC,
    "textile, chambray": _TWC,
    "textile, lace": _TWC,
    "textile, velvet": _TWC,
    "textile, polyester": "textile, nonwoven polyester",
    "textile, recycled polyester": "textile, nonwoven polyester",
    "textile, viscose": _FV,
    "textile, linen": _TWC,
    "textile, linen blend": _TWC,
    "textile, hemp": _CFH,
    "textile, hemp blend": _CFH,
    "textile, bamboo": _FV,
    "textile, Tencel": _FV,
    "textile, brocade": "textile, silk",
    "textile, chiffon": "textile, silk",
    "textile, silk blend": "textile, silk",
    "textile, elastane": _PU,
    "textile, woven acrylic": _FP,
    "textile, woven wool": "sheep fleece in the grease",
    "yarn, acrylic": "yarn, cotton",
    "yarn, cashmere": "yarn, silk",
    "yarn, cashmere (not listed, assumed similar to silk)": "yarn, silk",
    "yarn, elastane": "yarn, cotton",
    "yarn, elastane (not listed, assumed 1.50 kg CO2eq/kg)": "yarn, cotton",
    "yarn, flax": "yarn, cotton",
    "yarn, linen": "yarn, cotton",
    "yarn, merino wool": "yarn, silk",
    "yarn, nylon 6": "nylon 6",
    "yarn, organic cotton": "yarn, cotton",
    "yarn, polyester": "yarn, cotton",
    "yarn, wool": "yarn, silk",
    "yarn, wool, conventional, at farm gate": _WC,
    "ylene vinyl acetate copolymer": _EVA,
}

# Pre-compiled patterns for resolution
_PREFIX_RE = re.compile(
    r"^(?:canopy|frame|handle|ribs|fabric|sunbrella|"
    r"water-resistant|waterproof):\s*",
    re.IGNORECASE,
)
_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")


def resolve_static(name: str) -> Optional[str]:
    """Resolve a material name via static synonym map.

    Resolution strategy:
    1. Direct lookup in SYNONYM_MAP
    2. Strip component prefix and retry
    3. Strip trailing parenthetical annotation and retry
    4. Return None if no match found

    Returns:
        Canonical reference name, or None if no alias found.
    """
    if not name:
        return None

    stripped = name.strip()

    if stripped in SYNONYM_MAP:
        return SYNONYM_MAP[stripped]

    deprefixed = _PREFIX_RE.sub("", stripped)
    if deprefixed != stripped and deprefixed in SYNONYM_MAP:
        return SYNONYM_MAP[deprefixed]

    deparen = _PAREN_RE.sub("", stripped).strip()
    if deparen != stripped and deparen in SYNONYM_MAP:
        return SYNONYM_MAP[deparen]

    return None
