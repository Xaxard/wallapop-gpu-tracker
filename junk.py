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

# ----------------------------------------------------------------- phones
# Everything below only fires on a listing that names an Apple phone, so none
# of it can touch a GPU listing. "pantalla" in particular is a normal word in a
# graphics-card description ("no da imagen en pantalla") and a fatal one in a
# phone title, where it means a replacement screen rather than a handset.

APPLE_TOKENS = ("iphone", "apple")

# The product is an accessory that merely names the phone it fits. Matched only
# when one of these *leads* the title — "iPhone 15 Pro con funda y cargador" is
# a phone being sold with extras, while "Funda Silicona iPhone 15 Pro Max" is a
# 23 EUR case that would otherwise classify as a 700 EUR handset.
PHONE_ACCESSORY_LEADS = (
    "funda", "fundas", "carcasa", "carcasas", "protector", "protectores",
    "cristal", "cristales", "templado", "cargador", "cargadores", "cable",
    "cables", "adaptador", "soporte", "correa", "powerbank", "airpods",
    "airtag", "dock", "auriculares", "caja", "cajas", "filtro", "filtros",
    "kit", "pack", "lote", "anillo", "grip", "tripode", "estuche",
)

# Spare parts. Same rule, same reason.
PHONE_PART_LEADS = (
    "pantalla", "pantallas", "display", "placa", "tapa", "modulo", "flex",
    "altavoz", "chasis", "camara", "lente", "bateria", "baterias", "conector",
    "boton", "vibrador", "antena",
)

# Counterfeits. A replica is not a cheap iPhone, it is a different product, and
# at 150 EUR against a 700 EUR reference it looks like the best deal of the day.
PHONE_FAKE = (
    "replica", "replicas", "clon", "clonico", "copia", "imitacion",
    "no original", "tipo iphone", "estilo iphone", "similar al iphone",
)

# Repair shops advertising a service, priced at the cost of the repair. "Cambio
# Batería iPhone 17e - 69€" reads as a 69 EUR iPhone 17e against a 580 EUR
# reference, which is why these dominated the first live run of phone alerts.
PHONE_SERVICE = (
    "cambio de bateria", "cambio bateria", "cambio de pantalla", "cambio pantalla",
    "reparacion", "reparaciones", "reparamos", "reparo", "arreglo", "arreglamos",
    "servicio tecnico", "cambiamos", "presupuesto sin compromiso", "mano de obra",
)

# Locked or blocked handsets. This is the one phone rule that fires anywhere in
# the text rather than only at the start, because it is a hard defect: an
# iCloud-locked or IMEI-blacklisted phone cannot be activated by anyone, so it
# is not a cheap phone, it is not a phone at all. Consistent with the rule that
# a card which still works is fine — these do not work.
PHONE_LOCKED = (
    "icloud",
    "bloqueado por icloud",
    "bloqueo de activacion",
    "cuenta de icloud",
    "buscar mi iphone activado",
    "imei bloqueado",
    "lista negra",
    "blacklist",
    "reportado como perdido",
    "no se puede activar",
    "pide cuenta",
    "solo para piezas",
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
_LAPTOP_PHRASES_RX = _compile(LAPTOP_PHRASES)
_PHONE_LOCKED_RX = _compile(PHONE_LOCKED)
_PHONE_FAKE_RX = _compile(PHONE_FAKE)
_PHONE_SERVICE_RX = _compile(PHONE_SERVICE)


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


def _mentions_apple(norm_text: str) -> bool:
    return any(re.search(rf"\b{t}\b", norm_text) for t in APPLE_TOKENS)


def _first_index(tokens: list[str], words: tuple[str, ...]) -> tuple[int, str | None]:
    """Position of the earliest listed word, and which one it was."""
    best, hit = len(tokens), None
    for i, token in enumerate(tokens):
        if token in words and i < best:
            best, hit = i, token
    return best, hit


def _accessory_before_product(norm_title: str, words: tuple[str, ...]) -> str | None:
    """The accessory/part word, when it is named *before* the phone itself.

    Word order is what separates the product from things sold for it, and it is
    far more reliable than checking only the first token. Every junk listing
    names the accessory first and the handset second, because the handset is
    the qualifier: "Funda Silicona iPhone 15 Pro", "Pack Fundas iPhone 15 Pro
    Max", "Ringke Funda Magnética para iPhone 15 Plus", "3 Protectores Pantalla
    Cristal Templado iPhone 16", "Caja iPhone 15 Pro Max". Every genuine
    listing does the reverse, whatever precedes it: "iPhone 15 Pro con funda",
    "Vendo mi iPhone 15 Pro con funda y cargador".

    A leading-token check missed all five junk examples above, because brand
    names, quantities and stars get there first.
    """
    tokens = norm_title.split()
    phone_at, _ = _first_index(tokens, APPLE_TOKENS)
    word_at, word = _first_index(tokens, words)
    return word if word is not None and word_at < phone_at else None


def _phone_verdict(norm_title: str, haystack: str) -> JunkVerdict | None:
    """Phone-only rules. Returns None when nothing applies.

    Gated on the listing naming an Apple phone at all, so a graphics card whose
    description happens to say "pantalla" or "cable" is never touched by any of
    this.
    """
    if not _mentions_apple(haystack):
        return None

    hit = _first_hit(haystack, _PHONE_LOCKED_RX)
    if hit:
        return JunkVerdict(True, hit, "LOCKED")

    hit = _first_hit(haystack, _PHONE_FAKE_RX)
    if hit:
        return JunkVerdict(True, hit, "FAKE")

    # Services are advertised in the title; a genuine seller's description may
    # well mention having had the battery changed, which is a selling point.
    hit = _first_hit(norm_title, _PHONE_SERVICE_RX)
    if hit:
        return JunkVerdict(True, hit, "SERVICE")

    word = _accessory_before_product(norm_title, PHONE_ACCESSORY_LEADS)
    if word:
        return JunkVerdict(True, word, "ACCESSORY")
    word = _accessory_before_product(norm_title, PHONE_PART_LEADS)
    if word:
        return JunkVerdict(True, word, "PART")
    return None


def check(title: str | None, description: str | None = None) -> JunkVerdict:
    """Return why a listing should be dropped, or CLEAN."""
    norm_title = normalise(title)
    norm_desc = normalise(description) if description else ""
    haystack = f"{norm_title} {norm_desc}".strip()

    # Phones first, and they take the whole decision: an Apple listing must not
    # then be run through the GPU form-factor lists, whose bare tokens were
    # chosen on the assumption that every listing is a graphics card.
    if _mentions_apple(norm_title):
        verdict = _phone_verdict(norm_title, haystack)
        if verdict is not None:
            return verdict
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

    return _shared_rules(norm_title, haystack)


def _shared_rules(norm_title: str, haystack: str) -> JunkVerdict:
    """Rules that hold whatever the product is: wanted ads, LEER, defects.

    A broken phone and a broken card are both worthless for the same reason, and
    "busco" opens a wanted ad in either category.
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
