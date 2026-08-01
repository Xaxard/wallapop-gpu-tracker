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


BRAND_TOKENS = (
    "rtx", "gtx", "nvidia", "geforce", "radeon", "rx", "amd", "gpu",
    "grafica", "graficas", "tarjeta grafica", "vga",
)

VRAM_RE = re.compile(r"\b(4|6|8|10|11|12|16|20|24|32)\s*(?:gb|g|gigas?)\b")


@dataclass(frozen=True)
class ModelDef:
    key: str
    display: str
    pattern: str
    # When set, the match only counts if the title's VRAM equals this value.
    vram: int | None = None


def _num(n: str, *suffix: str) -> str:
    """Pattern for a GPU number plus optional suffix words.

    Allows an optional leading brand token and tolerates the separators people
    actually type ("rtx4070ti", "RTX 4070 TI", "4070-Ti").
    """
    parts = [rf"\b{n}"]
    for s in suffix:
        parts.append(rf"\s+{s}")
    return "".join(parts) + r"\b"


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

    @property
    def priceable(self) -> bool:
        """Only high/medium matches may drive the margin engine."""
        return self.model_key is not None and self.confidence in ("high", "medium")


NO_MATCH = Match(None, None, "none")


def extract_vram(norm_text: str) -> int | None:
    """Largest plausible VRAM figure in the text.

    Largest wins because titles often carry both the card's VRAM and unrelated
    numbers ("...para PC 16 gb RAM, grafica 8 gb") — but for the split SKUs we
    only ever compare against 8/16, and the card's own VRAM is normally the one
    quoted next to the model.
    """
    hits = [int(m.group(1)) for m in VRAM_RE.finditer(norm_text)]
    return max(hits) if hits else None


def _confidence(norm_text: str, has_vram_proof: bool) -> str:
    """High when a brand token backs the number; lower for bare digits."""
    has_brand = any(re.search(rf"\b{re.escape(b)}\b", norm_text) for b in BRAND_TOKENS)
    if has_brand:
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
            return Match(model.key, model.display, _confidence(norm_title, True), vram)
        return Match(model.key, model.display, _confidence(norm_title, False), vram)

    return NO_MATCH
