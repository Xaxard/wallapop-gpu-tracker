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

from models import CONSOLE_LEAD_NOUNS, normalise

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

# Desktop CPU model numbers, which collide with Radeon numbers head-on: a
# "Ryzen 5 7600X" is a processor and an "RX 7600 XT" is a graphics card, and the
# digits are identical.
#
# CPU_TOKENS above was documented as the defence for this and is not: it only
# fires on the literal words "procesador"/"microprocesador", and the laptop
# regex below only catches *mobile* chips (the h/hs/hx suffixes). A plain
# "AMD Ryzen 5 7600X" hit neither, matched \b7600\b, and — because "amd" is a
# valid AMD brand token — classified as an RX 7600 at *high* confidence,
# priceable, against a ~200 EUR reference. Verified live on 2026-08-26.
#
# Kept narrow in two ways, because the original comment's caution still stands
# ("ideal para Ryzen 5000" is a real GPU listing):
#   * the number must have a CPU's shape — 4-5 digits (Ryzen 7600, Intel
#     12400) with an optional x/x3d/g/k/f
#     suffix — so a bare series like "ryzen 5000" does not match;
#   * it is skipped entirely when the title also names a GPU vendor, so
#     "RX 6700 compatible con Ryzen 7 5800X" survives.
DESKTOP_CPU_RX = re.compile(
    r"\b(?:ryzen\s*[3579]|i\s*[3579])\s*\d{4,5}\s*(?:x3d|xt|x|g|ge|k|kf|kd|f)?\b"
)

# GPU vendor words that make a CPU model number incidental rather than the
# product. Deliberately excludes bare "amd", which is exactly what a Ryzen box
# says.
GPU_VENDOR_TOKENS = ("rtx", "gtx", "geforce", "radeon", "rx", "nvidia")

# Other PC components sold on the same searches. None of these are graphics
# cards, and none were being filtered — "Placa base B550", "Disco duro SSD 1TB"
# and "Pack cables PSU" all reached the bootstrap alert path clean, where a
# keyword match and a price under the cap is the whole test.
#
# Title-only, and skipped when the title opens by naming a card, for the same
# reason the laptop rules are: a graphics card listing may legitimately mention
# these in passing ("RTX 3080 con disipador nuevo", "incluyo disco duro").
# Matching them against the description would reject real cards for describing
# what comes in the box.
COMPONENT_TOKENS = (
    "placa",          # "placa base"
    "disipador",
    "ssd",
    "nvme",
    "disco",
)

COMPONENT_PHRASES = (
    "placa base",
    "disco duro",
    "pack cables",
    "bloque refrigeracion",
    "refrigeracion liquida",
    # A 2006 GeForce, not a Radeon RX 7600. The rival-vendor rule in models.py
    # already drops "Nvidia 7600GS" to low confidence; this stops it earlier and
    # covers the variant that names no vendor at all. normalise() splits the
    # letter/digit boundary, so the stored phrase is "7600 gs".
    "7600 gs",
    # Old Radeon, out of the tracked registry entirely.
    "vega rx",
    "rx vega",
)

# Phone accessories and services, not phones. These are the dominant noise on
# any iPhone search: a "Funda iPhone 15" at 8 EUR against a 700 EUR reference is
# a 99% margin by the maths and pure junk in reality — it is the listing named
# in db.log_junk's own docstring as what filled that table with 2.86M rows.
#
# Split in two, because this file's founding rule is that a single word may
# never exclude. "iPhone 15 con funda incluida" is a real phone sold with a
# case, and banning the bare word "funda" kills it — a working listing rejected
# for describing an extra, and invisible, because exclusions are never alerted.
#
# So an accessory noun only excludes when the title *opens* with it, which is
# how these listings are actually written ("Funda iPhone 15 Pro"). "Replica" and
# "clon" sit here rather than in the phrase list for the same reason: sellers
# write "100% original, no replica" constantly.
PHONE_ACCESSORY_PREFIXES = frozenset({
    "funda", "fundas", "carcasa", "protector", "protectores",
    "cargador", "cargadores", "cable", "cables", "adaptador",
    "pantalla", "pantallas", "bateria", "baterias", "tapa",
    "camara", "placa", "soporte", "cristal", "replica", "clon",
    "maqueta", "imitacion", "repuesto", "repuestos",
})

# ---------------------------------------------------------------- consoles
# PS5 and Xbox Series X are tracked for comps only (family 'console',
# alert_loop.ALERTING_FAMILIES), and the noise on a console search is unlike
# anything else this bot searches. Sampling `ps5` and `xbox series x` live on
# 2026-08-26, roughly four listings in five carrying the console's name were a
# *game* or an accessory: "Elden Ring PS5", "Mando DualSense PS5", "FIFA 23
# para Xbox Series X", and — genuinely — "Nevera Consola XBOX Series X 10L", a
# novelty fridge.
#
# That matters more here than a bad GPU alert would. A game is a real listing
# at a real price that really sells, so it does not look wrong to any of the
# sale-inference machinery; it just quietly drags a console's reference price
# toward 70 EUR. The pool has no way to notice.
#
# Note normalise() has already destroyed the slash that makes cross-platform
# listings obvious to a human: "PS4/PS5" arrives as "ps 4 ps 5" and "Series
# X/S" as "series x s". The rules below work on that shape, not on the
# punctuation.

# Nouns that open an accessory listing. Prefix-only, following the same
# founding rule as PHONE_ACCESSORY_PREFIXES: "Xbox Series X 1TB SSD con 2
# mandos" is a console sold with controllers and must survive, while "Mando
# Xbox Series X" must not.
CONSOLE_ACCESSORY_PREFIXES = frozenset({
    "mando", "mandos", "control", "controlador", "controladores", "dualsense",
    "dualshock", "auricular", "auriculares", "cascos", "volante", "volantes",
    "funda", "fundas", "carcasa", "carcasas", "soporte", "soportes", "base",
    "cargador", "cargadores", "cable", "cables", "adaptador", "grip", "grips",
    "protector", "protectores", "bateria", "ventilador", "refrigerador",
    "dock", "estacion", "kit", "steelbook", "poster", "posters", "pegatina",
    "pegatinas", "camiseta", "taza", "llavero", "lampara", "mochila", "logo",
    "nevera", "figura", "funko", "maqueta", "replica", "skin", "vinilo",
    "juego", "juegos", "disco", "caja", "cristal", "tapa", "recambio",
})

# Words that only ever belong to a game or an accessory. Skipped when the title
# leads with the console itself, because "Consola Xbox Series X + 2 Mandos y
# Juegos" is a console being sold with its games.
CONSOLE_NOT_A_CONSOLE_TOKENS = (
    "juego", "juegos", "mando", "mandos", "dualsense", "dualshock",
    "auriculares", "cascos", "volante", "steelbook", "funda", "carcasa",
    "grips", "nevera", "funko",
)

# "para PS5" means the listing is *for* the console, not the console. A console
# listing never says it.
CONSOLE_FOR_RX = re.compile(
    r"\bpara\s+(?:la\s+|el\s+|tu\s+)?"
    r"(?:ps\s*[45]|play\s*station\s*[45]|playstation\s*[45]|xbox|consola)\b"
)

# One listing, two platform generations, means a game — a console is exactly
# one platform. This is the single most reliable rule here, and it survives
# normalise() flattening the slash.
PLATFORM_RXS = {
    "ps5":       re.compile(r"\bps\s*5\b|\bplay\s*(?:station\s*)?5\b"),
    "ps4":       re.compile(r"\bps\s*4\b|\bplay\s*(?:station\s*)?4\b"),
    "ps3":       re.compile(r"\bps\s*3\b|\bplay\s*(?:station\s*)?3\b"),
    "ps2":       re.compile(r"\bps\s*2\b|\bplay\s*(?:station\s*)?2\b"),
    "psp":       re.compile(r"\bpsp\b|\bps\s*vita\b"),
    "xbox_sx":   re.compile(r"\bseri[ea]s?\s+x\b"),
    "xbox_ss":   re.compile(r"\bseri[ea]s?\s+s\b|\bseri[ea]s?\s+x\s+s\b"),
    "xbox_one":  re.compile(r"\bxbox\s+one\b"),
    "xbox_360":  re.compile(r"\bxbox\s+360\b"),
    "nintendo":  re.compile(r"\bnintendo\b|\bswitch\b|\bwii\b"),
    "pc":        re.compile(r"\bsteam\s+deck\b"),
}


# Phrases that mean "not a sellable handset" wherever they appear. Multi-word,
# so each carries its own context and cannot straddle an innocent sentence.
PHONE_NOT_A_PHONE = (
    "protector de pantalla",
    "cristal templado",
    "cambio de pantalla",
    "reparacion de",
    "reparamos",
    "pantalla para",
    "bateria para",
    "cargador para",
    "tapa trasera",
    "camara para",
    "solo la placa",
    "placa base",
    # An iCloud-locked handset cannot legally be resold and goes for a fraction
    # of a working one. Letting one price the pool drags the reference down for
    # every clean handset of that model.
    "libre de icloud",
    "bloqueado por icloud",
    "bloqueado icloud",
    "cuenta icloud",
)


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
_PHONE_NOT_A_PHONE_RX = _compile(PHONE_NOT_A_PHONE)
_LAPTOP_PHRASES_RX = _compile(LAPTOP_PHRASES)
_COMPONENT_PHRASES_RX = _compile(COMPONENT_PHRASES)

# Any mention of a tracked console, used only to route into the console branch.
_CONSOLE_TOKEN_RX = re.compile(
    r"\bps\s*5\b|\bplay\s*(?:station\s*)?5\b"
    r"|\bxbox\b(?=.*\bseri[ea]s?\s+[xs]\b)|\bseri[ea]s?\s+x\b(?=.*\bxbox\b)"
)


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


def _is_phone_title(norm_title: str) -> bool:
    """Does this title name an iPhone?

    Deliberately just the word. Every phone rule that follows is an accessory or
    service pattern, so a false positive here costs nothing — a graphics card
    whose title says "iphone" is not a graphics card either — while a false
    negative lets "Funda iPhone 15" reach the margin engine at a 99% margin.
    """
    return re.search(r"\biphone\b", norm_title) is not None


def _leads_with_card_noun(norm_title: str) -> bool:
    """True when the title opens by naming a graphics card."""
    head = " ".join(norm_title.split()[:3])
    return any(head.startswith(noun) for noun in CARD_NOUNS)


def _is_console_title(norm_title: str) -> bool:
    """Does this title name a PS5 or an Xbox Series console?

    Like _is_phone_title, a false positive is cheap: every console rule that
    follows is an accessory, game or cross-platform pattern, and a graphics card
    whose title says "ps5" is not a graphics card either.
    """
    return _CONSOLE_TOKEN_RX.search(norm_title) is not None


def _leads_with_console_noun(norm_title: str) -> bool:
    """True when the title opens by naming the console itself.

    This is the single most useful signal on a console search, and it comes
    straight from how the two kinds of listing are actually written. A console
    leads with what it is — "Consola PS5 con mando", "Xbox Series X 1TB SSD con
    2 mandos", "Ps5 pro 2tb blanca sin caja". A game leads with the game —
    "Elden Ring PS5", "Hogwarts Legacy Xbox Series X" — and mentions the
    platform afterwards, because that is the word buyers search for.
    """
    head = " ".join(norm_title.split()[:3])
    return any(head.startswith(noun) for noun in CONSOLE_LEAD_NOUNS)


def _platforms_named(norm_title: str) -> int:
    """How many distinct platform generations this title names."""
    return sum(1 for rx in PLATFORM_RXS.values() if rx.search(norm_title))


def check(title: str | None, description: str | None = None) -> JunkVerdict:
    """Return why a listing should be dropped, or CLEAN."""
    norm_title = normalise(title)
    norm_desc = normalise(description) if description else ""
    haystack = f"{norm_title} {norm_desc}".strip()

    # Phone rules first, and only for titles naming an iPhone. A phone listing
    # has nothing to do with the bundle/laptop/CPU rules below, and those rules
    # have nothing to say about it.
    if _is_phone_title(norm_title):
        words = norm_title.split()
        if words and words[0] in PHONE_ACCESSORY_PREFIXES:
            return JunkVerdict(True, words[0], "NOT_A_PHONE")
        hit = _first_hit(norm_title, _PHONE_NOT_A_PHONE_RX)
        if hit:
            return JunkVerdict(True, hit, "NOT_A_PHONE")
        return _shared_rules(norm_title, haystack)

    # Console rules, and only for titles naming a tracked console. Same shape as
    # the phone branch: these listings have nothing to do with the bundle,
    # laptop and CPU rules below, and those rules have nothing to say about
    # them.
    if _is_console_title(norm_title):
        words = norm_title.split()
        if words and words[0] in CONSOLE_ACCESSORY_PREFIXES:
            return JunkVerdict(True, words[0], "NOT_A_CONSOLE")

        # Two platforms named at once is a game, whatever else the title says —
        # this one is not skipped for console-led titles, because "Xbox Series
        # X/S" leads with the console and is still a game.
        if _platforms_named(norm_title) > 1:
            return JunkVerdict(True, "multi-platform", "NOT_A_CONSOLE")

        if CONSOLE_FOR_RX.search(norm_title):
            return JunkVerdict(True, "para", "NOT_A_CONSOLE")

        # The remaining words are only decisive when the title does not lead
        # with the console: a console legitimately ships "con 2 mandos y juegos".
        if not _leads_with_console_noun(norm_title):
            tokens = set(words)
            for token in CONSOLE_NOT_A_CONSOLE_TOKENS:
                if token in tokens:
                    return JunkVerdict(True, token, "NOT_A_CONSOLE")

        return _shared_rules(norm_title, haystack)

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
        if not any(v in tokens for v in GPU_VENDOR_TOKENS):
            cpu_hit = DESKTOP_CPU_RX.search(norm_title)
            if cpu_hit:
                return JunkVerdict(True, cpu_hit.group(0), "CPU")
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

        for token in COMPONENT_TOKENS:
            if token in tokens:
                return JunkVerdict(True, token, "COMPONENT")
        hit = _first_hit(norm_title, _COMPONENT_PHRASES_RX)
        if hit:
            return JunkVerdict(True, hit, "COMPONENT")

    return _shared_rules(norm_title, haystack)


def _shared_rules(norm_title: str, haystack: str) -> JunkVerdict:
    """Rules that hold whatever the product is: wanted ads, LEER, defects.

    Split out from check() so the wanted-ad, LEER and defect rules stay in one
    place regardless of which product-specific branch ran first.
    """
    for prefix in WANTED_PREFIXES:
        if norm_title.startswith(prefix + " ") or norm_title == prefix:
            return JunkVerdict(True, prefix, "WANTED")

    leer_hit = LEER_RX.search(norm_title)
    if leer_hit:
        return JunkVerdict(True, leer_hit.group(0), "LEER")

    for category, compiled in _PHRASE_GROUPS_RX:
        hit = _first_hit(haystack, compiled)
        if hit:
            return JunkVerdict(True, hit, category)

    return CLEAN
