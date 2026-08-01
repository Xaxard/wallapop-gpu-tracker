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


def test_sub_50_eur_gpu_is_never_a_real_deal():
    """A real GPU under 50 EUR is broken, fake, or bait — never a genuine
    flip, so it's rejected outright rather than treated as a great margin."""
    assert not pricing.sane(49.99)
    assert not pricing.sane(30)
    assert pricing.sane(50)


# ------------------------------------------------------------------- gating
def test_evaluate_gates_on_the_offer_price_not_the_asking_price(monkeypatch):
    """qualifies iff a haggled offer (asking * (1 - OFFER_DISCOUNT)) clears the
    learned buy_ceiling — not the raw asking price. ceiling=224, discount=20%
    means any asking price up to 224/0.8=280 should qualify.
    """
    monkeypatch.setattr(config, "OFFER_DISCOUNT", 0.20)
    row = {"ref_price": 330, "buy_ceiling": 224.0, "buy_ceiling_in_person": 280.0, "n_comps": 12}
    assert pricing.evaluate(210, row, None).qualifies       # offer 168 <= 224
    assert pricing.evaluate(250, row, None).qualifies       # offer 200 <= 224 — rescued by haggling
    assert not pricing.evaluate(290, row, None).qualifies   # offer 232 > 224 — still too rich


def test_offer_discount_rescues_a_listing_that_fails_at_asking_price(monkeypatch):
    """The whole point of the offer-based gate: even when the asking price
    alone wouldn't have cleared the old ceiling check, the listing still
    qualifies if a plausible 20% haggle would get there — you can always
    propose the discount to the seller and see if it lands.
    """
    monkeypatch.setattr(config, "OFFER_DISCOUNT", 0.20)
    row = {"ref_price": 450, "buy_ceiling": 367.27, "buy_ceiling_in_person": 400.0, "n_comps": 20}
    deal = pricing.evaluate(400, row, None)  # 400 > 367.27 asking, but offer 320 <= 367.27
    assert deal.qualifies
    assert deal.offer_price == pytest.approx(320.0)


def test_evaluate_reports_net_margin_at_offer_and_at_asking(monkeypatch):
    # Pin the fee model so a local .env override can't change the expectation.
    monkeypatch.setattr(config, "SHIPPED", config.FeeModel(0.0, 0.075, 0.69, 4.50, 50.0, "s"))
    monkeypatch.setattr(config, "OFFER_DISCOUNT", 0.20)
    row = {"ref_price": 330, "buy_ceiling": 224.0, "buy_ceiling_in_person": 280.0, "n_comps": 12}
    deal = pricing.evaluate(280, row, None)  # asking 280, offer = 280*0.8 = 224
    assert deal.offer_price == pytest.approx(224.0)
    # net at offer = 330 - (224*1.075 + 0.69 + 4.50) = 84.01
    assert deal.net_shipped == pytest.approx(84.01, abs=0.05)
    # net at asking = 330 - (280*1.075 + 0.69 + 4.50) = 23.81 (below the 50 target the old gate required)
    assert deal.net_shipped_at_asking == pytest.approx(23.81, abs=0.05)
    # in-person has zero fees either way: ref - buy
    assert deal.net_in_person == pytest.approx(106.0, abs=0.05)


def test_evaluate_falls_back_to_bootstrap_cap():
    """Without a learned reference, there's no margin to haggle against — the
    bootstrap path stays a plain asking-price cap."""
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


@pytest.mark.parametrize(
    "title",
    [
        "ASUS ProArt P16 – RTX 4070 / 64GB RAM / 2TB SSD",
        "Asus ProArt PX13 Ryzen 9 RTX 4060",
        "Portátil Asus ProArt Studiobook 16",
    ],
)
def test_proart_laptops_are_excluded(title):
    """ProArt P16/PX13 are laptop model numbers, unlike bare 'ProArt'."""
    verdict = junk.check(title)
    assert verdict.excluded and verdict.category == "LAPTOP"


def test_proart_graphics_card_is_not_mistaken_for_a_laptop():
    """ProArt is also a real ASUS desktop-GPU sub-brand — must survive."""
    verdict = junk.check("ASUS ProArt GeForce RTX 4080 OC 16GB")
    assert not verdict.excluded, f"wrongly excluded on {verdict.phrase!r}"


def test_exclusion_reports_phrase_and_category():
    verdict = junk.check("RTX 3070 para piezas")
    assert verdict.phrase == "para piezas"
    assert verdict.category == "DEFECT"


@pytest.mark.parametrize(
    "title",
    [
        "Procesador AMD Ryzen 5 7600 6 nucleos 12 hilos",
        "Microprocesador Ryzen 5 5600X",
    ],
)
def test_cpu_listings_are_excluded(title):
    """Regression: Ryzen 5 7600 (CPU) numerically collides with Radeon RX 7600
    (GPU) with no differentiating suffix on either side — a real production
    listing was misclassified as a confident 'DEAL' on RX 7600 before this fix.
    The filter is deliberately narrow ("procesador"/"microprocesador" only, not
    the bare "ryzen"/"amd" brand names) so it doesn't cost real GPU listings
    that merely mention CPU compatibility.
    """
    verdict = junk.check(title)
    assert verdict.excluded and verdict.category == "CPU"


def test_bare_ryzen_mention_does_not_exclude():
    """'ryzen' alone isn't in CPU_TOKENS — a title naming it without
    "procesador" isn't confidently a CPU listing, and being too strict here
    risks dropping real GPU deals."""
    assert not junk.check("AMD Ryzen 7 7700X sin usar").excluded


def test_card_mentioning_a_compatible_cpu_survives():
    verdict = junk.check("Tarjeta grafica RTX 4070 ideal para Ryzen 5000")
    assert not verdict.excluded, f"wrongly excluded on {verdict.phrase!r}"
