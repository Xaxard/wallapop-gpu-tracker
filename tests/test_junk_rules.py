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
