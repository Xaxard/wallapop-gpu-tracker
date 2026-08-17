"""GPU title -> canonical model_key parser.

Comps are only meaningful if a "4070" is never pooled with a "4070 Ti Super",
so every listing is normalised to a canonical key before it can be priced.

Rules:
  * most-specific pattern wins (ti super > ti > super > base), so the registry
    is ordered and the first match returns;
  * VRAM disambiguates the split SKUs (4060 Ti / 5060 Ti 8GB vs 16GB); when the
    title doesn't say, it falls back to a separate generic key rather than
    guessing into one of the two pools;
  * confidence is 'high' only when a brand token (rtx/geforce/radeon/rx/...)
    backs up the number. A bare "4070" in a noisy title returns 'low', and the
    caller must not price a low-confidence match.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


# --------------------------------------------------------------- normalising
def normalise(text: str | None) -> str:
    """Lowercase, strip accents, collapse punctuation into single spaces.

    Also splits letter/digit runs ("4060ti" -> "4060 ti", "8gb" -> "8 gb") so a
    single spaced pattern matches every way people write these names.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z])(\d)", r"\1 \2", text)
    return re.sub(r"\s+", " ", text).strip()


# "amd" stays in, even though AMD also makes Ryzen CPUs whose model numbers
# can collide with Radeon GPU numbers (Ryzen 5 7600 vs RX 7600) — junk.py's
# CPU_TOKENS filter is the line of defense for that case. Dropping "amd" here
# would cost real AMD GPU listings that don't literally say "rx"/"radeon".
BRAND_TOKENS = (
    "rtx", "gtx", "nvidia", "geforce", "radeon", "rx", "amd", "gpu",
    "grafica", "graficas", "tarjeta grafica", "vga",
)

# Brand tokens that belong to one vendor, used to stop a *rival's* branding
# from validating a match. Bare model numbers collide across vendors — AMD's
# "RX 7600" is 2023, NVIDIA's "GeForce 7600 GS" is 2006 — and without this a
# listing reading "PC de escritorio HP P4 + Nvidia 7600GS" (a real listing, at
# 60 EUR) classifies as an RX 7600, prices against a 200 EUR reference, and
# reports a 143 EUR margin on a twenty-year-old card.
AMD_TOKENS = ("rx", "radeon", "amd")
NVIDIA_TOKENS = ("rtx", "gtx", "nvidia", "geforce")
APPLE_TOKENS = ("iphone", "apple")

VRAM_RE = re.compile(r"\b(4|6|8|10|11|12|16|20|24|32)\s*(?:gb|g|gigas?)\b")

# Phone storage. 1TB is written every possible way, so it normalises to "1tb".
# Deliberately excludes the small sizes a modern iPhone never ships with —
# "64 gb" in an iPhone 15 title is almost always an unrelated number.
STORAGE_RE = re.compile(r"\b(128|256|512)\s*gb\b|\b(1)\s*(?:tb|t)\b")


@dataclass(frozen=True)
class ModelDef:
    key: str
    display: str
    pattern: str
    # When set, the match only counts if the title's VRAM equals this value.
    vram: int | None = None
    # Which product family this is. Drives the price cap, the brand-consistency
    # check, and which junk rules are allowed to fire.
    family: str = "gpu"


def _num(n: str, *suffix: str) -> str:
    """Pattern for a GPU number plus optional suffix words.

    Allows an optional leading brand token and tolerates the separators people
    actually type ("rtx4070ti", "RTX 4070 TI", "4070-Ti").
    """
    parts = [rf"\b{n}"]
    for s in suffix:
        parts.append(rf"\s+{s}")
    return "".join(parts) + r"\b"


def _iphone(*parts: str) -> str:
    """Pattern for an iPhone model name.

    The brand token is *required*, unlike the GPU patterns. A GPU number like
    "4070" is distinctive enough to stand alone, but "15" is not — a bare
    `\\b15\\b` would classify "Cargador 15W" and "RTX 4070 15 unidades" as
    iPhones. Apple is also accepted because a few sellers write "Apple 15 Pro".

    normalise() splits letter/digit runs, so "iPhone15" and "16e" arrive as
    "iphone 15" and "16 e"; "promax" has no digit boundary to split on, so both
    spellings are matched explicitly.
    """
    body = r"\s+".join(parts)
    return rf"\b(?:iphone|apple)\s+{body}\b"


_PRO_MAX = r"(?:pro\s+max|promax)"

# Generated rather than written out: 13 near-identical entries in strict
# most-specific-first order is exactly the kind of list where a hand-typed
# ordering slip silently mis-prices a whole generation.
def _generation(num: str, *, variants: tuple[str, ...]) -> tuple[ModelDef, ...]:
    out: list[ModelDef] = []
    for suffix, label, pattern in variants:
        key = f"iphone_{num}" + (f"_{suffix}" if suffix else "")
        display = f"iPhone {num}" + (f" {label}" if label else "")
        out.append(ModelDef(key, display, _iphone(num, pattern) if pattern else _iphone(num),
                            family="phone"))
    return tuple(out)


_GEN_15_16 = (
    ("pro_max", "Pro Max", _PRO_MAX),
    ("pro", "Pro", "pro"),
    ("plus", "Plus", "plus"),
    ("", "", ""),
)
# The Plus was retired after the 16; the 17 line replaces it with the Air.
_GEN_17 = (
    ("pro_max", "Pro Max", _PRO_MAX),
    ("pro", "Pro", "pro"),
    ("", "", ""),
)

_IPHONES: tuple[ModelDef, ...] = (
    # 17 series (Sept 2025) + iPhone 17e (Mar 2026)
    ModelDef("iphone_17e", "iPhone 17e", _iphone("17", "e"), family="phone"),
    # Apple's own name is just "iPhone Air", but sellers routinely slot it into
    # the generation they think of it as ("iPhone 17 Air"), so both parse. It
    # has to sit *above* the 17 generation for the same reason "pro max" sits
    # above "pro": otherwise "iPhone 17 Air" matches the bare 17 first and
    # prices a ~800 EUR phone against a ~700 EUR one.
    ModelDef("iphone_air", "iPhone Air", r"\b(?:iphone|apple)\s+(?:17\s+)?air\b",
             family="phone"),
    *_generation("17", variants=_GEN_17),
    # 16 series (Sept 2024) + iPhone 16e (Feb 2025)
    ModelDef("iphone_16e", "iPhone 16e", _iphone("16", "e"), family="phone"),
    *_generation("16", variants=_GEN_15_16),
    # 15 series (Sept 2023)
    *_generation("15", variants=_GEN_15_16),
)


# Ordered most-specific first. First match wins.
REGISTRY: tuple[ModelDef, ...] = (
    # ---------------------------------------------------------- RTX 50 series
    ModelDef("rtx_5090", "RTX 5090", _num("5090")),
    ModelDef("rtx_5080", "RTX 5080", _num("5080")),
    ModelDef("rtx_5070_ti", "RTX 5070 Ti", _num("5070", "ti")),
    ModelDef("rtx_5070", "RTX 5070", _num("5070")),
    ModelDef("rtx_5060_ti_16g", "RTX 5060 Ti 16GB", _num("5060", "ti"), vram=16),
    ModelDef("rtx_5060_ti_8g", "RTX 5060 Ti 8GB", _num("5060", "ti"), vram=8),
    ModelDef("rtx_5060_ti", "RTX 5060 Ti", _num("5060", "ti")),
    ModelDef("rtx_5060", "RTX 5060", _num("5060")),
    # ---------------------------------------------------------- RTX 40 series
    ModelDef("rtx_4090", "RTX 4090", _num("4090")),
    ModelDef("rtx_4080_super", "RTX 4080 Super", _num("4080", "super")),
    ModelDef("rtx_4080", "RTX 4080", _num("4080")),
    ModelDef("rtx_4070_ti_super", "RTX 4070 Ti Super", _num("4070", "ti", "super")),
    ModelDef("rtx_4070_ti", "RTX 4070 Ti", _num("4070", "ti")),
    ModelDef("rtx_4070_super", "RTX 4070 Super", _num("4070", "super")),
    ModelDef("rtx_4070", "RTX 4070", _num("4070")),
    ModelDef("rtx_4060_ti_16g", "RTX 4060 Ti 16GB", _num("4060", "ti"), vram=16),
    ModelDef("rtx_4060_ti_8g", "RTX 4060 Ti 8GB", _num("4060", "ti"), vram=8),
    ModelDef("rtx_4060_ti", "RTX 4060 Ti", _num("4060", "ti")),
    ModelDef("rtx_4060", "RTX 4060", _num("4060")),
    # ---------------------------------------------------------- RTX 30 series
    ModelDef("rtx_3090_ti", "RTX 3090 Ti", _num("3090", "ti")),
    ModelDef("rtx_3090", "RTX 3090", _num("3090")),
    ModelDef("rtx_3080_ti", "RTX 3080 Ti", _num("3080", "ti")),
    ModelDef("rtx_3080_12g", "RTX 3080 12GB", _num("3080"), vram=12),
    ModelDef("rtx_3080", "RTX 3080", _num("3080")),
    ModelDef("rtx_3070_ti", "RTX 3070 Ti", _num("3070", "ti")),
    ModelDef("rtx_3070", "RTX 3070", _num("3070")),
    ModelDef("rtx_3060_ti", "RTX 3060 Ti", _num("3060", "ti")),
    ModelDef("rtx_3060_12g", "RTX 3060 12GB", _num("3060"), vram=12),
    ModelDef("rtx_3060", "RTX 3060", _num("3060")),
    ModelDef("rtx_3050", "RTX 3050", _num("3050")),
    # ------------------------------------------------------------ AMD RX 9000
    ModelDef("rx_9070_xt", "RX 9070 XT", _num("9070", "xt")),
    ModelDef("rx_9070", "RX 9070", _num("9070")),
    ModelDef("rx_9060_xt_16g", "RX 9060 XT 16GB", _num("9060", "xt"), vram=16),
    ModelDef("rx_9060_xt_8g", "RX 9060 XT 8GB", _num("9060", "xt"), vram=8),
    ModelDef("rx_9060_xt", "RX 9060 XT", _num("9060", "xt")),
    # ------------------------------------------------------------ AMD RX 7000
    ModelDef("rx_7900_xtx", "RX 7900 XTX", _num("7900", "xtx")),
    ModelDef("rx_7900_xt", "RX 7900 XT", _num("7900", "xt")),
    ModelDef("rx_7900_gre", "RX 7900 GRE", _num("7900", "gre")),
    ModelDef("rx_7800_xt", "RX 7800 XT", _num("7800", "xt")),
    ModelDef("rx_7700_xt", "RX 7700 XT", _num("7700", "xt")),
    ModelDef("rx_7600_xt", "RX 7600 XT", _num("7600", "xt")),
    ModelDef("rx_7600", "RX 7600", _num("7600")),
    # ------------------------------------------------------------ AMD RX 6000
    ModelDef("rx_6800_xt", "RX 6800 XT", _num("6800", "xt")),
    ModelDef("rx_6800", "RX 6800", _num("6800")),
    ModelDef("rx_6750_xt", "RX 6750 XT", _num("6750", "xt")),
    ModelDef("rx_6700_xt", "RX 6700 XT", _num("6700", "xt")),
    ModelDef("rx_6650_xt", "RX 6650 XT", _num("6650", "xt")),
    ModelDef("rx_6600_xt", "RX 6600 XT", _num("6600", "xt")),
    ModelDef("rx_6600", "RX 6600", _num("6600")),
    # ----------------------------------------------------------------- iPhones
    # 15 series onwards. Ordered most-specific first within each generation:
    # "pro max" must be tried before "pro", and "pro" before the bare number,
    # or an iPhone 15 Pro Max classifies as an iPhone 15 and prices ~270 EUR low.
    *_IPHONES,
)

_COMPILED = tuple((m, re.compile(m.pattern)) for m in REGISTRY)

DISPLAY = {m.key: m.display for m in REGISTRY}

# Generic keys used when a title omits VRAM, mapped to the specific SKUs they
# sit between. pricing.py borrows those pools when the generic key is short of
# comps of its own.
GENERIC_FALLBACKS = {
    "rtx_4060_ti": ("rtx_4060_ti_8g", "rtx_4060_ti_16g"),
    "rtx_5060_ti": ("rtx_5060_ti_8g", "rtx_5060_ti_16g"),
    "rx_9060_xt": ("rx_9060_xt_8g", "rx_9060_xt_16g"),
    # Single-variant VRAM splits. Listed here for the same reason as the pairs
    # above, but the load-bearing effect is in comps_loop: only models reachable
    # from a search (directly or through this map) are eligible to be judged
    # absent, so without an entry a 3060 12GB listing would never be closed and
    # could never contribute a sold comp.
    "rtx_3060": ("rtx_3060_12g",),
    "rtx_3080": ("rtx_3080_12g",),
}


def same_family(search_key: str, item_key: str | None) -> bool:
    """Does a classified listing satisfy a search that targeted `search_key`?

    Exact match, or the generic/specific pairing of a split-VRAM SKU — a search
    for `rx_9060_xt` must still accept a listing that classified as
    `rx_9060_xt_16g`.
    """
    if item_key is None:
        return False
    if item_key == search_key:
        return True
    if item_key in GENERIC_FALLBACKS.get(search_key, ()):
        return True
    return search_key in GENERIC_FALLBACKS.get(item_key, ())


@dataclass(frozen=True)
class Match:
    model_key: str | None
    display: str | None
    confidence: str  # 'high' | 'medium' | 'low' | 'none'
    vram: int | None = None
    family: str | None = None
    storage: str | None = None   # phones: '128gb' | '256gb' | '512gb' | '1tb'

    @property
    def priceable(self) -> bool:
        """Only high/medium matches may drive the margin engine."""
        return self.model_key is not None and self.confidence in ("high", "medium")


NO_MATCH = Match(None, None, "none")

FAMILY_BY_KEY = {m.key: m.family for m in REGISTRY}


def family_of(model_key: str | None) -> str | None:
    """Family for a stored model_key, for callers that only kept the key."""
    return FAMILY_BY_KEY.get(model_key or "")


def extract_vram(norm_text: str) -> int | None:
    """Largest plausible VRAM figure in the text.

    Largest wins because titles often carry both the card's VRAM and unrelated
    numbers ("...para PC 16 gb RAM, grafica 8 gb") — but for the split SKUs we
    only ever compare against 8/16, and the card's own VRAM is normally the one
    quoted next to the model.
    """
    hits = [int(m.group(1)) for m in VRAM_RE.finditer(norm_text)]
    return max(hits) if hits else None


def extract_storage(norm_text: str) -> str | None:
    """Phone storage tier, normalised to '128gb' / '256gb' / '512gb' / '1tb'.

    Smallest wins, the opposite of extract_vram. Phone titles quote the
    storage they are selling, but bundle-ish listings and accessory cross-sells
    add larger numbers ("iPhone 15 128GB + funda, tarjeta 512GB de regalo"), and
    over-reading storage inflates the reference price rather than deflating it.
    """
    order = {"128gb": 0, "256gb": 1, "512gb": 2, "1tb": 3}
    hits: list[str] = []
    for m in STORAGE_RE.finditer(norm_text):
        hits.append("1tb" if m.group(2) else f"{m.group(1)}gb")
    return min(hits, key=lambda h: order[h]) if hits else None


def _has_token(norm_text: str, tokens: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(t)}\b", norm_text) for t in tokens)


def _confidence(norm_text: str, has_vram_proof: bool, key: str) -> str:
    """High when a brand token backs the number; lower for bare digits.

    The brand has to belong to the *right* vendor. A rival's branding next to a
    colliding model number is evidence against the match, not for it: "Nvidia
    7600GS" is a 2006 GeForce, not a Radeon RX 7600, and treating nvidia as
    generic proof made it a high-confidence AMD match worth 200 EUR.

    A wrong-vendor title drops to 'low', which `Match.priceable` refuses, so it
    can never reach the margin engine. It is not rejected outright — the number
    still matched, and a listing can legitimately name both vendors ("cambio mi
    RX 7600 por una Nvidia").
    """
    if FAMILY_BY_KEY.get(key) == "phone":
        # The pattern already required an "iphone"/"apple" token to match at
        # all, so reaching here *is* the brand proof. There is no rival vendor
        # to guard against — nobody else ships a product called an iPhone.
        return "high"

    own, rival = (
        (AMD_TOKENS, NVIDIA_TOKENS) if key.startswith("rx_") else (NVIDIA_TOKENS, AMD_TOKENS)
    )
    if _has_token(norm_text, own):
        return "high"
    if _has_token(norm_text, rival):
        return "low"
    # Only generic tokens (grafica, gpu, vga...) — no vendor claim either way.
    if _has_token(norm_text, BRAND_TOKENS):
        return "high"
    if has_vram_proof:
        return "medium"
    return "low"


def classify(title: str | None, description: str | None = None) -> Match:
    """Map a listing title (with the description as a weak tiebreak) to a model."""
    norm_title = normalise(title)
    if not norm_title:
        return NO_MATCH

    # VRAM is read from the title first; the description is a fallback because
    # it's far noisier (system RAM, other parts in a bundle, ...).
    vram = extract_vram(norm_title)
    if vram is None and description:
        vram = extract_vram(normalise(description))

    # Registry order does the disambiguation: every VRAM-gated entry sits above
    # its generic fallback, which in turn sits above the shorter base-model
    # pattern, so the first match is always the most specific one.
    for model, rx in _COMPILED:
        if not rx.search(norm_title):
            continue
        if model.vram is not None:
            if vram != model.vram:
                continue
            confidence = _confidence(norm_title, True, model.key)
        else:
            confidence = _confidence(norm_title, False, model.key)
        # Storage is title-only. Unlike VRAM there is no fallback to the
        # description: phone descriptions list iCloud sizes, SD cards and the
        # seller's other handsets, and a wrong tier moves the price by ~150 EUR.
        storage = extract_storage(norm_title) if model.family == "phone" else None
        return Match(
            model.key, model.display, confidence, vram,
            family=model.family, storage=storage,
        )

    return NO_MATCH
