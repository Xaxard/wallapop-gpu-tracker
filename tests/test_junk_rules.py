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


# ------------------------------------------------------------- phone rules
@pytest.mark.parametrize(
    "title,category",
    [
        ("Funda Silicona iPhone 15 Pro Max MagSafe", "ACCESSORY"),
        ("Protector pantalla iPhone 15", "ACCESSORY"),
        ("Cargador iPhone 20W original", "ACCESSORY"),
        ("Cristal templado iPhone 16 Pro", "ACCESSORY"),
        ("Pantalla iPhone 15 Pro original", "PART"),
        ("Bateria iPhone 15 Pro Max nueva", "PART"),
        ("Placa base iPhone 16", "PART"),
    ],
)
def test_phone_accessories_and_parts_are_dropped(title, category):
    """These name the phone they fit and would otherwise classify as a handset
    worth 20-30x their price — a 23 EUR case reading as a 700 EUR iPhone."""
    verdict = junk.check(title, "")
    assert verdict.excluded
    assert verdict.category == category


def test_a_phone_sold_with_extras_is_still_a_phone():
    """The leading-noun rule. "iPhone 15 Pro con funda y cargador" leads with
    the product; only a listing that *opens* with the accessory is one."""
    assert not junk.check("iPhone 15 Pro con funda y cargador incluidos", "todo original").excluded
    assert not junk.check("iPhone 15", "incluye protector de pantalla y cable").excluded


@pytest.mark.parametrize(
    "description",
    [
        "bloqueado por icloud, no se puede activar",
        "imei bloqueado, lista negra",
        "pide cuenta del anterior dueño, bloqueo de activacion",
    ],
)
def test_locked_handsets_are_dropped_wherever_it_is_stated(description):
    """The one phone rule that fires anywhere in the text. An iCloud-locked or
    blacklisted phone cannot be activated by anyone, so it is not a cheap phone
    — it is not a usable phone at all."""
    verdict = junk.check("iPhone 15 Pro Max 256GB", description)
    assert verdict.excluded
    assert verdict.category == "LOCKED"


def test_cosmetic_and_battery_wear_still_pass():
    """Same principle as the GPU side: as long as it works, it is a candidate."""
    assert not junk.check("iPhone 15 Pro 128GB", "Bateria 87%, pantalla impecable").excluded
    assert not junk.check(
        "iPhone 16 Pro Max 512GB", "Pequeño arañazo en la pantalla pero funciona perfecto"
    ).excluded


def test_phone_rules_never_touch_a_graphics_card():
    """"pantalla", "cable" and "soporte" are ordinary words in a GPU listing and
    fatal ones in a phone title, so every phone rule is gated on the listing
    naming an Apple phone."""
    assert not junk.check(
        "Tarjeta Grafica RTX 4070", "incluye cable de alimentacion, probada en pantalla 4k"
    ).excluded
    assert not junk.check("RTX 3080 Ventus", "comprada hace 1 año funciona perfecta").excluded
    # A card whose description mentions a phone must not pick up phone rules
    # either — the gate is the *title* naming an Apple product.
    assert not junk.check(
        "Tarjeta Grafica RTX 4070", "acepto cambio parcial, tengo un iPhone que me interesa"
    ).excluded


def test_shared_rules_still_apply_to_phones():
    """Wanted ads, LEER and outright defects are product-agnostic."""
    assert junk.check("Busco iPhone 15 Pro", "pago al momento").category == "WANTED"
    assert junk.check("LEER iPhone 15 Pro", "").category == "LEER"
    assert junk.check("iPhone 15 Pro Max", "no funciona, para piezas").category == "DEFECT"
