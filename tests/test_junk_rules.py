"""Two additions to the junk filter: the "no da video" defect phrase and the
title-only "LEER" rule. See junk.py for the reasoning; these tests just pin
the behaviour, especially the regression guard against the description's
legitimate "leer la descripcion".
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import junk  # noqa: E402


# ----------------------------------------------------------------- no da video
@pytest.mark.parametrize(
    "title,description",
    [
        ("RTX 3080 Ti enciende pero no da video", None),
        ("Gigabyte RTX 3070 Ti", "Enciende pero no da video, para piezas o reparar"),
    ],
)
def test_no_da_video_is_excluded(title, description):
    verdict = junk.check(title, description)
    assert verdict.excluded and verdict.category == "DEFECT"


# ----------------------------------------------------------------------- LEER
@pytest.mark.parametrize(
    "title",
    [
        "LEER Gigabyte RTX 3080 Ti Tarjeta Grafica",
        "(LEERRR) URGE VENTA Gigabyte AORUS RTX 3080 Ti",
    ],
)
def test_leer_titles_are_excluded(title):
    """Real listings — a shouted LEER in the title means there's a catch."""
    verdict = junk.check(title)
    assert verdict.excluded and verdict.category == "LEER"


def test_leer_in_description_does_not_exclude():
    """Regression guard: 'leer la descripcion' is boilerplate that appears in
    countless good listings and must never be checked against the LEER rule."""
    verdict = junk.check(
        "Tarjeta Grafica MSI RTX 4070",
        "Antes de preguntar, puedes leer la descripcion completa mas abajo.",
    )
    assert not verdict.excluded, f"wrongly excluded on {verdict.phrase!r} ({verdict.category})"


# ------------------------------------------------------------------- controls
@pytest.mark.parametrize(
    "title",
    [
        "Tarjeta Grafica MSI RTX 4070",
        "RTX 4070 Dual ASUS 12GB",
    ],
)
def test_ordinary_good_listings_stay_clean(title):
    verdict = junk.check(title)
    assert not verdict.excluded, f"wrongly excluded on {verdict.phrase!r} ({verdict.category})"


def test_cosmetic_oxidation_is_not_junk():
    """Owner's explicit principle: as long as the card works, it's fine. A
    cosmetically corroded HDMI port on an otherwise working card is a
    legitimate discount, not a defect — 'oxidada' must never be banned."""
    verdict = junk.check(
        "RTX 3070 en buen estado",
        "Un poco oxidada la placa donde se conecta el HDMI pero funciona bien.",
    )
    assert not verdict.excluded, f"wrongly excluded on {verdict.phrase!r} ({verdict.category})"


def test_sin_garantia_is_not_junk():
    """'sin garantia' usually just means no shop warranty, not a defect."""
    verdict = junk.check("RTX 4060 Ti sin garantia, particular")
    assert not verdict.excluded, f"wrongly excluded on {verdict.phrase!r} ({verdict.category})"


# ------------------------------------------------------------ phone rules
@pytest.mark.parametrize("title", [
    "Funda iPhone 15 Pro Max silicona",
    "Carcasa iPhone 16 transparente",
    "Pantalla para iPhone 15 original",
    "Cargador para iPhone 15 20W",
    "Cristal templado iPhone 17 Pro",
    "Replica iPhone 15 1:1",
    "Cambio de pantalla iPhone 15 en 30 minutos",
    "iPhone 16 Pro bloqueado por icloud",
])
def test_phone_accessories_and_services_are_excluded(title):
    """A "Funda iPhone 15" at 8 EUR against a 700 EUR reference is a 99% margin
    by the maths and pure junk in reality — the single biggest noise source on
    any phone search, and the listing db.log_junk's docstring blames for 2.86M
    audit rows."""
    verdict = junk.check(title)
    assert verdict.excluded
    assert verdict.category in ("NOT_A_PHONE", "DEFECT")


@pytest.mark.parametrize("title", [
    "iPhone 15 Pro Max 256GB impecable",
    "iPhone 15 con funda incluida",
    "iPhone 15 Pro Max, 100% original no replica",
    "iPhone 15 Plus 128GB con cargador y caja",
    "Apple iPhone 17 Pro Max 512GB precintado",
])
def test_real_phones_survive_the_accessory_rules(title):
    """The failure that matters. This file's founding rule is that a single word
    may never exclude: "iPhone 15 con funda incluida" is a phone sold *with* a
    case, and banning the bare word "funda" rejects a working listing for
    describing an extra — invisibly, because exclusions are never alerted on.
    So accessory nouns only exclude from the front of a title."""
    assert not junk.check(title).excluded


def test_phone_rules_do_not_touch_graphics_cards():
    """The phone branch is gated on the title naming an iPhone, so the accessory
    vocabulary cannot reach a GPU listing. "Pantalla" and "cargador" are the
    clearest cases: both are ordinary words in a card listing and both are
    junk-triggering at the front of a phone one.

    (Unrelated and pre-existing: "soporte vertical" is a GPU NOT_A_CARD phrase
    that fires anywhere in a title, so a card sold *with* a stand is excluded —
    the same shape as the "funda" problem fixed for phones above. Left alone
    deliberately; changing the GPU rules is not what this change is about.)
    """
    assert not junk.check("RTX 4070 Gigabyte, incluye cargador y cable").excluded
    assert not junk.check("Grafica RTX 4080 para pantalla 4K").excluded
    assert junk.check("Cargador para iPhone 15").excluded
    assert junk.check("Pantalla para iPhone 15").excluded


def test_gpu_rules_still_fire_after_the_phone_branch():
    """Guards the ordering: the phone check returns early, so the bundle and
    laptop rules must still be reachable for everything else."""
    assert junk.check("PC gaming con RTX 4070").category == "BUNDLE"
    assert junk.check("Portatil MSI RTX 4060").category == "LAPTOP"
