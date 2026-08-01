"""Margin maths, comps trimming, and the phrase-only junk filter."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import junk  # noqa: E402
import pricing  # noqa: E402


# ------------------------------------------------------------------ margins
def test_shipped_ceiling_matches_worked_example():
    """Spec §5.3: a 4070 at ref 330 must be <= ~224 shipped."""
    fees = config.FeeModel(0.10, 0.075, 0.69, 4.50, 50.0, "shipped")
    assert fees.buy_ceiling(330) == pytest.approx(224.0, abs=1.0)


def test_in_person_ceiling_is_ref_minus_target():
    fees = config.FeeModel(0.0, 0.0, 0.0, 0.0, 50.0, "in person")
    assert fees.buy_ceiling(330) == pytest.approx(280.0)


def test_buying_at_the_ceiling_nets_exactly_the_target():
    fees = config.FeeModel(0.10, 0.075, 0.69, 4.50, 50.0, "shipped")
    ceiling = fees.buy_ceiling(330)
    assert fees.net_margin(ceiling, 330) == pytest.approx(50.0)


# -------------------------------------------------------------------- comps
def test_trimmed_median_drops_outliers():
    # One typo price and one bundle must not move the answer.
    prices = [30.0, 300, 310, 315, 320, 325, 330, 335, 340, 1500.0]
    assert pricing.trimmed_median(prices, trim=0.10) == pytest.approx(322.5)


def test_trim_is_skipped_on_tiny_samples():
    assert pricing.trimmed_median([100, 200, 300], trim=0.10) == 200


def test_trimmed_median_of_empty_is_none():
    assert pricing.trimmed_median([]) is None


def test_sanity_band():
    assert not pricing.sane(5)
    assert not pricing.sane(99999)
    assert not pricing.sane(None)
    assert pricing.sane(300)


# ------------------------------------------------------------------- gating
def test_evaluate_uses_learned_ceiling():
    row = {"ref_price": 330, "buy_ceiling": 224.0, "buy_ceiling_in_person": 280.0, "n_comps": 12}
    assert pricing.evaluate(210, row, None).qualifies
    assert not pricing.evaluate(245, row, None).qualifies


def test_evaluate_reports_both_net_margins(monkeypatch):
    # Pin the fee model so a local .env override can't change the expectation.
    monkeypatch.setattr(config, "SHIPPED", config.FeeModel(0.10, 0.075, 0.69, 4.50, 50.0, "s"))
    row = {"ref_price": 330, "buy_ceiling": 224.0, "buy_ceiling_in_person": 280.0, "n_comps": 12}
    deal = pricing.evaluate(210, row, None)
    # 330*0.90 - (210*1.075 + 0.69 + 4.50) = 66.06
    assert deal.net_shipped == pytest.approx(66.06, abs=0.05)
    assert deal.net_in_person == pytest.approx(120.0, abs=0.05)


def test_hard_budget_ceiling_beats_a_great_margin(monkeypatch):
    """No alert above MAX_DEAL_PRICE even when the maths says it's a steal."""
    monkeypatch.setattr(config, "MAX_DEAL_PRICE", 350.0)
    row = {"ref_price": 1200, "buy_ceiling": 1050.0, "buy_ceiling_in_person": 1150.0, "n_comps": 20}
    assert not pricing.evaluate(700, row, None).qualifies
    assert pricing.evaluate(340, row, None).qualifies


def test_hard_ceiling_also_applies_to_bootstrap_caps(monkeypatch):
    monkeypatch.setattr(config, "MAX_DEAL_PRICE", 350.0)
    assert not pricing.evaluate(390, None, 400).qualifies


def test_evaluate_falls_back_to_bootstrap_cap():
    assert pricing.evaluate(180, None, 200).qualifies
    assert not pricing.evaluate(220, None, 200).qualifies


def test_evaluate_without_reference_or_cap_never_fires():
    assert not pricing.evaluate(180, None, None).qualifies


# --------------------------------------------------------------------- junk
@pytest.mark.parametrize(
    "title",
    [
        "RTX 3070 no funciona, para piezas",
        "Gráfica RTX 4070 NO ENCIENDE",
        "RTX 3080 para reparar",
        "Solo la caja de una RTX 4090",
        "Busco RTX 4070 barata",
        "COMPRO grafica rtx",
        "Waterblock para RTX 3080",
    ],
)
def test_junk_is_excluded(title):
    assert junk.check(title).excluded


@pytest.mark.parametrize(
    "title",
    [
        "RTX 3070 perfecto estado, no acepto cambios",
        "RTX 4070 Ti, no busco cambios, solo venta",
        "RTX 4060 funciona perfectamente",
        "RTX 3080 con caja y accesorios",
        "RX 7800 XT probada en varios juegos",
    ],
)
def test_good_listings_survive(title):
    verdict = junk.check(title)
    assert not verdict.excluded, f"wrongly excluded on {verdict.phrase!r}"


def test_wanted_words_only_count_at_the_start():
    assert junk.check("Busco RTX 4070").excluded
    assert not junk.check("RTX 4070 barata, busco comprador serio").excluded


@pytest.mark.parametrize(
    "title",
    [
        "PC Gaming i7-14700kf 32GB RAM RTX 4070 Super",
        "Ordenador gaming Ryzen 5 + RTX 3070",
        "Torre gaming completa con RTX 4060 Ti",
        "PC completo RTX 3080",
    ],
)
def test_whole_pc_bundles_are_excluded(title):
    """A tower priced at 850 EUR is not a comp for a loose GPU."""
    verdict = junk.check(title)
    assert verdict.excluded and verdict.category == "BUNDLE"


@pytest.mark.parametrize(
    "title",
    [
        "Tarjeta gráfica RTX 4070 para PC gaming",
        "Gráfica RTX 3070 ideal para pc gaming",
        "GPU RTX 4060 Ti sacada de un pc gaming",
    ],
)
def test_cards_that_merely_mention_a_pc_survive(title):
    assert not junk.check(title).excluded


@pytest.mark.parametrize(
    "title",
    [
        "Lenovo Legion Pro 5 rtx 4070",
        "Portátil Asus TUF F15 RTX 4070",
        "AORUS 17 RTX4070 - 2K 240Hz 16gb DDR5",
        "MSI Sword 17 HX RTX 4070 i7 14650HX",
        "ASUS ProArt P16 – RTX 4070 / 64GB RAM / 2TB SSD",
        "Asus TUF A15 Ryzen 9 RTX 4070 32GB RAM",
        "Lenovo Legion 7i Gen 9 (RTX 4070)",
        "ALIENWARE M16 R2 RTX 4070",
        "HP Victus 16 RTX 4060",
    ],
)
def test_laptops_are_excluded(title):
    """Real titles from the live API — a 2600 EUR laptop is not a 4070 comp."""
    verdict = junk.check(title)
    assert verdict.excluded and verdict.category == "LAPTOP"


@pytest.mark.parametrize(
    "title",
    [
        # The AIB brand words deliberately kept out of the laptop token list.
        "RX 7900 XTX Sapphire Nitro+ 16GB",
        "RTX 4070 ti asus tuf gaming oc 12gb",
        "ASUS ROG Strix RTX 3080 OC",
        "Gigabyte RTX 4070 AORUS Elite 12GB",
        "Sapphire RX 7800 XT Pulse",
        "MSI RTX 4070 Ventus 3X",
        "Powercolor RX 9060 XT Hellhound 16GB",
        "Tarjeta gráfica RTX 3070 de un portátil",
    ],
)
def test_desktop_cards_are_not_mistaken_for_laptops(title):
    verdict = junk.check(title)
    assert not verdict.excluded, f"wrongly excluded on {verdict.phrase!r} ({verdict.category})"


def test_exclusion_reports_phrase_and_category():
    verdict = junk.check("RTX 3070 para piezas")
    assert verdict.phrase == "para piezas"
    assert verdict.category == "DEFECT"
