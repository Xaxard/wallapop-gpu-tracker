"""Phrase-only junk filter (spec §8).

Deliberately never bans a single word: "roto", "piezas", "cambio" and friends
appear constantly in perfectly good listings ("no acepto cambios", "sin piezas
que falten"), so only multi-word phrases can exclude. `busco`/`compro`/`se
busca` are wanted-ads and only count at the *start* of a title, which keeps a
seller writing "...no busco cambios" safe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from models import normalise

# Phrases are matched against the accent-stripped, punctuation-collapsed text,
# so they're written here in the same normalised form (no accents).
DEFECT = (
    "no funciona",
    "para piezas",
    "no enciende",
    "no arranca",
    "no da imagen",
    "no da video",  # "enciende pero no da video" is the "no da imagen" variant
                    # that was missing — live testing caught real dead cards
                    # (powers on, no video output) passing the filter clean and
                    # topping the bot's own highest-margin alerts.
    "sin funcionar",
    "para reparar",
    "no probada",
    "no probado",
    "non testee",  # foreign-language relistings turn up in Spanish results
    "solo caja",
    "caja vacia",
    "solo la caja",
    "no se vende",
)

TRADE = (
    "cambio por",
    "solo cambio",
)

NOT_A_CARD = (
    "solo disipador",
    "solo ventilador",
    "waterblock",
    "backplate",
    "solo soporte",
    "soporte vertical",
    "soporte grafica",      # the 11 EUR mounting bracket, not the card
    "soporte para grafica",
    "soporte gpu",
    "riser",
)

# Whole-PC / bundle listings. These name a GPU in the title and classify to it
# perfectly, but "RTX 4070 Super" at 850 EUR inside a full tower is not a comp
# for a loose 4070 Super — left in, they drag every median upwards and blow up
# the ceilings. Title-only, because a card's *description* often mentions the PC
# it came out of.
BUNDLE = (
    "pc gaming",
    "pc gamer",
    "ordenador gamer",
    "torre gamer",
    "equipo gamer",
    "pc completo",
    "pc sobremesa",
    "pc montado",
    "pc torre",
    "ordenador gaming",
    "ordenador sobremesa",
    "ordenador completo",
    "equipo completo",
    "equipo gaming",
    "torre gaming",
    "sobremesa gaming",
    "setup completo",
)

# If one of these leads the title, it's a card being sold that merely mentions
# the kind of PC it suits ("Grafica RTX 4070 para PC gaming") — not a bundle.
CARD_NOUNS = ("tarjeta grafica", "tarjeta", "grafica", "graficas", "gpu", "vga")

# Gaming laptops are the worst comps polluter of all: they name a desktop GPU in
# the title and sell for 850-2600 EUR, which would roughly double a 4070's
# median. Bare tokens are used here — against the phrase-only rule that governs
# the defect list — but only for words that are laptop *product lines* and can
# never appear on a graphics card. Deliberately excluded from this list are the
# AIB brand words that would cause false hits: nitro (Sapphire Nitro+), rog,
# tuf, strix, aorus, pulse, ventus, eagle, phantom, hellhound.
LAPTOP_TOKENS = (
    "portatil", "portatiles", "notebook", "laptop",
    "legion", "zephyrus", "thinkpad", "ideapad", "vivobook", "zenbook",
    "macbook", "victus", "alienware", "helios", "katana",
    "cyborg", "elitebook", "probook", "latitude", "inspiron", "pavilion",
)

LAPTOP_PHRASES = (
    "ordenador portatil",
    "gaming laptop",
    "portatil gaming",
    "omen by hp",
    "predator helios",
    "rog strix g",
    "proart studiobook",
)

# CPU listings, not GPUs — AMD Ryzen model numbers can numerically collide
# with Radeon GPU model numbers with no differentiating suffix (Ryzen 5 "7600"
# vs Radeon RX "7600"), which let a processor get misclassified as a graphics
# card. Deliberately narrow (not "ryzen" or "amd"): those brand names can
# legitimately appear in a GPU title mentioning compatibility ("ideal para
# Ryzen 5000"), and being too strict here costs real GPU deals. "procesador"/
# "microprocesador" only ever show up when the product itself is a CPU.
CPU_TOKENS = ("procesador", "microprocesador")

# Patterns are matched against the *normalised* title, where normalise() has
# already split letter/digit runs ("i7 14650HX" -> "i 7 14650 hx").
LAPTOP_REGEXES = (
    # Laptop line followed by a screen size — "Aorus 17", "Sword 17", "Omen 16".
    # The lookahead stops "Nitro+ 16 GB" (a real graphics card) from matching.
    r"\b(?:aorus|omen|sword|raider|stealth|vector|titan|swift|nitro|predator|"
    r"crosshair|delta|modern|prestige|summit)\s*1[3-8]\b(?!\s*(?:gb|g)\b)",
    # Asus TUF A15/F15 laptops. TUF *cards* are "TUF Gaming OC", never "TUF A15",
    # so this stays clear of the GPU brand.
    r"\btuf\s*[af]\s*1[3-8]\b",
    # Asus ProArt *laptops* are the P-series ("ProArt P16", "ProArt PX13").
    # "proart" alone is left out of LAPTOP_TOKENS because ProArt is also a real
    # desktop GPU sub-brand ("ASUS ProArt GeForce RTX 4080 OC") — only the
    # laptop model-number pattern is banned, not the bare word.
    r"\bproart\s+px?\s*1[3-8]\b",
    # Mobile CPU suffixes — i7 14650HX, Ryzen 7 7840HS.
    r"\b(?:i\s*[3579]|ryzen\s*[3579])\s*\d{4,5}\s*(?:hx|hs|h)\b",
    # Explicit screen size.
    r"\b1[3-8]\s*(?:pulgadas|inch)\b",
)

_LAPTOP_RX = tuple(re.compile(p) for p in LAPTOP_REGEXES)

# A shouted "LEER" ("read [this]") in the *title* is Spanish-marketplace
# shorthand for "there's a catch, read the description" and in practice flags
# a real defect — "LEER Gigabyte RTX 3080 Ti Tarjeta Grafica" and "(LEERRR)
# URGE VENTA Gigabyte AORUS RTX 3080 Ti" are both real listings that hid a
# fault below the fold. Title only, deliberately: the *description* legitimately
# says "leer la descripcion" / "puedes leer mas abajo" constantly, and banning
# it there would gut half the listing pool. normalise() already lowercases and
# strips punctuation, so "(LEERRR)" arrives here as "leerrr". The shape is
# l + one-or-more e + one-or-more r, which catches leer/leeer/leerr/leerrr,
# but the leading/trailing \b keeps it off real words that merely contain the
# substring, e.g. "releer" (starts with r, not l) or "leerlo" (trailing "lo"
# breaks the boundary).
LEER_RX = re.compile(r"\ble+r+\b")

# Only rejected when the title *starts* with one of these.
WANTED_PREFIXES = (
    "busco",
    "compro",
    "se busca",
    "se compra",
    "necesito",
)

PHRASE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DEFECT", DEFECT),
    ("TRADE", TRADE),
    ("NOT_A_CARD", NOT_A_CARD),
)


# Phrases are matched on word boundaries, never as bare substrings. A plain
# `phrase in haystack` straddles words and silently kills good listings:
# normalise() strips the tilde from "año", so the extremely ordinary Spanish
# sentence "comprada hace 1 año, funciona perfecta" becomes
# "... 1 ano funciona perfecta", in which "a[no funciona]" matches DEFECT's
# "no funciona" as a substring. That is the worst possible failure — a working
# card, described as working, excluded for saying so, and invisible because
# exclusions are never alerted on.
def _compile(phrases: tuple[str, ...]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple((p, re.compile(rf"\b{re.escape(p)}\b")) for p in phrases)


_PHRASE_GROUPS_RX = tuple(
    (category, _compile(phrases)) for category, phrases in PHRASE_GROUPS
)
_BUNDLE_RX = _compile(BUNDLE)
_LAPTOP_PHRASES_RX = _compile(LAPTOP_PHRASES)


def _first_hit(
    haystack: str, compiled: tuple[tuple[str, re.Pattern[str]], ...]
) -> str | None:
    for phrase, rx in compiled:
        if rx.search(haystack):
            return phrase
    return None


@dataclass(frozen=True)
class JunkVerdict:
    excluded: bool
    phrase: str | None = None
    category: str | None = None


CLEAN = JunkVerdict(False)


def _leads_with_card_noun(norm_title: str) -> bool:
    """True when the title opens by naming a graphics card."""
    head = " ".join(norm_title.split()[:3])
    return any(head.startswith(noun) for noun in CARD_NOUNS)


def check(title: str | None, description: str | None = None) -> JunkVerdict:
    """Return why a listing should be dropped, or CLEAN."""
    norm_title = normalise(title)

    # Form-factor checks run on the title only and are skipped when the title
    # opens by naming a card, so "Gráfica RTX 4070 sacada de un portátil" stays.
    if not _leads_with_card_noun(norm_title):
        hit = _first_hit(norm_title, _BUNDLE_RX)
        if hit:
            return JunkVerdict(True, hit, "BUNDLE")

        tokens = set(norm_title.split())
        for token in CPU_TOKENS:
            if token in tokens:
                return JunkVerdict(True, token, "CPU")
        for token in LAPTOP_TOKENS:
            if token in tokens:
                return JunkVerdict(True, token, "LAPTOP")
        hit = _first_hit(norm_title, _LAPTOP_PHRASES_RX)
        if hit:
            return JunkVerdict(True, hit, "LAPTOP")
        for rx in _LAPTOP_RX:
            hit = rx.search(norm_title)
            if hit:
                return JunkVerdict(True, hit.group(0), "LAPTOP")

    for prefix in WANTED_PREFIXES:
        if norm_title.startswith(prefix + " ") or norm_title == prefix:
            return JunkVerdict(True, prefix, "WANTED")

    leer_hit = LEER_RX.search(norm_title)
    if leer_hit:
        return JunkVerdict(True, leer_hit.group(0), "LEER")

    haystack = norm_title
    if description:
        haystack = f"{norm_title} {normalise(description)}"

    for category, compiled in _PHRASE_GROUPS_RX:
        hit = _first_hit(haystack, compiled)
        if hit:
            return JunkVerdict(True, hit, category)

    return CLEAN
