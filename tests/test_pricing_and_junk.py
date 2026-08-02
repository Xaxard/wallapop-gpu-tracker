"""Margin maths, comps trimming, and the phrase-only junk filter."""

import statistics
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import junk  # noqa: E402
import pricing  # noqa: E402


# ----------------------------------------------------- weighted-comps helpers
class _FakeDB:
    """Minimal stand-in for db.Database, scoped to what pricing.py reads:
    reserved_comps / sold_comps / get_model_prices / upsert_model_price.
    """

    def __init__(self, reserved=None, sold=None, model_prices=None):
        self._reserved = reserved or []
        self._sold = sold or []
        self._model_prices = dict(model_prices or {})
        self.writes = []

    def reserved_comps(self, model_key, since):
        return self._reserved

    def sold_comps(self, model_key, since):
        return self._sold

    def get_model_prices(self):
        return self._model_prices

    def upsert_model_price(self, row):
        self.writes.append(row)
        self._model_prices[row["model_key"]] = row


def _reserved_row(item_id, price, age_days=0.0):
    seen_at = (pricing.now() - timedelta(days=age_days)).isoformat()
    return {"item_id": item_id, "price": price, "seen_at": seen_at}


def _sold_row(item_id, price, age_days=0.0):
    closed_at = (pricing.now() - timedelta(days=age_days)).isoformat()
    return {"item_id": item_id, "sold_price": price, "closed_at": closed_at}


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


# ------------------------------------------------------- trim dead-zone fix
@pytest.mark.parametrize("n", range(5, 10))
def test_trim_dead_zone_fixed_for_min_comps_regime(n):
    """Old formula: k = int(n * TRIM_FRACTION) is 0 for every n in [5, 9] —
    exactly MIN_COMPS through roughly double it, the size a freshly-priced
    model sits at for a while — so a sample in this range got zero outlier
    protection. round() plus a floor of 1 must engage here instead.
    """
    assert int(n * config.TRIM_FRACTION) == 0            # the bug, confirmed still true of the naive formula
    assert pricing._trim_k(n, config.TRIM_FRACTION) >= 1  # the fix


def test_trim_k_still_respects_the_tiny_sample_floor():
    # n=3: dropping k=1 from each end would leave only 1 item, under the
    # n-2k>=3 safety guard, so trimming must not fire — unchanged by the fix.
    assert pricing.trimmed_median([100.0, 200.0, 300.0], trim=0.10) == 200


def test_trim_is_a_no_op_when_trim_fraction_is_zero():
    assert pricing._trim_k(7, 0.0) == 0


def test_trimmed_median_is_provably_invariant_to_symmetric_trim():
    """Why the fix above doesn't change trimmed_median()'s *output* at
    n=5..9: dropping k items off each end of a sorted list never moves which
    order statistic(s) the plain median falls on, so trimmed_median() equals
    the untrimmed statistics.median() any time the guard lets it trim at
    all — trimming-then-taking-a-median is a no-op by construction. The
    bug fix earns its keep on the *weighted* pool instead (see
    test_weighted_trim_kills_a_heavily_weighted_outlier_at_n7 below), where
    a heavily-weighted outlier's removal genuinely shifts the interpolated
    quantile that survives it.
    """
    values = [30.0, 300.0, 310.0, 315.0, 320.0]  # n=5, one bad typo price
    assert pricing.trimmed_median(values, trim=0.10) == pricing.trimmed_median(values, trim=0.0)


def test_weighted_trim_kills_a_heavily_weighted_outlier_at_n7():
    """A heavily-weighted (e.g. very recent) but obviously wrong low price
    would otherwise anchor the low end of the weighted interpolation and
    drag the whole quantile down with it — the "single outlier does the
    most damage in a small sample" scenario the dead-zone bug left
    unprotected. n=7 sits squarely in the [5, 9] regime the fix targets.
    """
    pairs = [
        (10.0, 10.0), (300.0, 1.0), (305.0, 1.0), (310.0, 1.0),
        (315.0, 1.0), (320.0, 1.0), (2000.0, 1.0),
    ]
    untrimmed = pricing.weighted_quantile(pairs, 0.5)
    trimmed = pricing.weighted_quantile(pricing._trim_pairs(pairs, config.TRIM_FRACTION), 0.5)
    assert untrimmed == pytest.approx(168.18, abs=0.01)  # the outlier drags it way down
    assert trimmed == pytest.approx(310.0)               # trimmed, it lands on the real cluster


# --------------------------------------------------------- weighted_quantile
def test_weighted_quantile_hand_computed():
    # Sorted: (10, w1), (20, w1), (30, w2); total weight 4. Midpoint
    # positions (cumulative weight so far minus half its own, over total):
    #   10 -> (1 - 0.5)/4 = 0.125
    #   20 -> (2 - 0.5)/4 = 0.375
    #   30 -> (4 - 1.0)/4 = 0.75
    # q=0.5 sits 1/3 of the way from the 20-slot to the 30-slot:
    #   20 + 1/3 * (30 - 20) = 23.333...
    pairs = [(30.0, 2.0), (10.0, 1.0), (20.0, 1.0)]
    assert pricing.weighted_quantile(pairs, 0.5) == pytest.approx(23.3333, abs=1e-3)


def test_weighted_quantile_matches_plain_median_at_equal_weights():
    values = [10.0, 40.0, 30.0, 20.0, 50.0]
    pairs = [(v, 1.0) for v in values]
    assert pricing.weighted_quantile(pairs, 0.5) == pytest.approx(statistics.median(values))


def test_weighted_quantile_extremes_and_empty():
    pairs = [(10.0, 1.0), (20.0, 3.0), (30.0, 1.0)]
    assert pricing.weighted_quantile(pairs, 0.0) == 10.0
    assert pricing.weighted_quantile(pairs, 1.0) == 30.0
    assert pricing.weighted_quantile([], 0.5) is None


# ------------------------------------------------------------- time decay
def test_time_decay_halves_at_the_configured_halflife():
    assert pricing._time_decay(0) == pytest.approx(1.0)
    assert pricing._time_decay(config.COMPS_HALFLIFE_DAYS) == pytest.approx(0.5)
    assert pricing._time_decay(2 * config.COMPS_HALFLIFE_DAYS) == pytest.approx(0.25)


def test_time_decay_is_monotonically_decreasing_with_age():
    weights = [pricing._time_decay(a) for a in (0, 5, 14, 30, 60)]
    assert weights == sorted(weights, reverse=True)
    assert weights[-1] < weights[0]


def test_collect_comps_weights_a_fresh_comp_above_a_stale_one():
    """Same price, same source, only age differs — GPU prices fall
    monotonically, so a 55-day-old comp must count for less than a
    2-day-old one, not the same."""
    db = _FakeDB(reserved=[
        _reserved_row("fresh", 300.0, age_days=2),
        _reserved_row("stale", 300.0, age_days=55),
    ])
    fresh_comp, stale_comp = pricing.collect_comps(db, "rtx_4070")
    assert fresh_comp.weight > stale_comp.weight
    assert fresh_comp.weight == pytest.approx(config.RESERVED_WEIGHT * pricing._time_decay(2), rel=1e-3)
    assert stale_comp.weight == pytest.approx(config.RESERVED_WEIGHT * pricing._time_decay(55), rel=1e-3)


# ------------------------------------------------------ sold vs. reserved
def test_sold_outranks_reserved_at_equal_age():
    """Reservations fall through, so a confirmed sale at the same age must
    outweigh a reservation — SOLD_WEIGHT > RESERVED_WEIGHT."""
    db = _FakeDB(
        reserved=[_reserved_row("r1", 300.0, age_days=5)],
        sold=[_sold_row("s1", 300.0, age_days=5)],
    )
    by_source = {c.source: c for c in pricing.collect_comps(db, "rtx_4070")}
    assert by_source["sold"].weight > by_source["reserved"].weight
    assert by_source["sold"].weight == pytest.approx(config.SOLD_WEIGHT * pricing._time_decay(5), rel=1e-3)
    assert by_source["reserved"].weight == pytest.approx(config.RESERVED_WEIGHT * pricing._time_decay(5), rel=1e-3)


def test_sale_overwrites_reservation_for_the_same_item():
    """Per-item dedup still holds: a card that was reserved then confirmed
    sold contributes once, as its sale."""
    db = _FakeDB(
        reserved=[_reserved_row("x", 250.0, age_days=1)],
        sold=[_sold_row("x", 260.0, age_days=1)],
    )
    comps = pricing.collect_comps(db, "rtx_4070")
    assert len(comps) == 1
    assert comps[0].source == "sold"
    assert comps[0].price == 260.0


def test_reserved_comp_sitting_for_weeks_still_counts_once():
    """500 observations of the same reserved card must not outvote real
    turnover — dedup by item_id, first hit (newest) wins."""
    db = _FakeDB(reserved=[_reserved_row("stuck", 300.0, age_days=d) for d in range(20)])
    comps = pricing.collect_comps(db, "rtx_4070")
    assert len(comps) == 1


# ------------------------------------------------------------- prior lookup
def test_prior_price_prefers_own_existing_ref_price():
    db = _FakeDB(model_prices={"rtx_4060_ti_8g": {"ref_price": 999.0}})
    assert pricing._prior_price(db, "rtx_4060_ti", {"ref_price": 280.0}) == pytest.approx(280.0)


def test_prior_price_falls_back_to_sibling_average_when_no_own_history():
    db = _FakeDB(model_prices={
        "rtx_4060_ti_8g": {"ref_price": 270.0},
        "rtx_4060_ti_16g": {"ref_price": 330.0},
    })
    assert pricing._prior_price(db, "rtx_4060_ti", None) == pytest.approx(300.0)


def test_prior_price_none_when_nothing_available():
    db = _FakeDB()
    assert pricing._prior_price(db, "rtx_4070", None) is None


# ---------------------------------------------------------------- shrinkage
def test_shrinkage_pulls_hard_toward_prior_with_few_comps(monkeypatch):
    """Right at MIN_COMPS, the sample is thin — the reference should land
    noticeably closer to the seed/prior than a naive median-only estimate
    would, replacing the old hard cliff (n=4 pure seed, n=5 pure observed)
    with a smooth blend."""
    monkeypatch.setattr(pricing, "_time_decay", lambda age: 1.0)  # isolate from clock noise
    reserved = [_reserved_row(f"r{i}", 300.0) for i in range(config.MIN_COMPS)]
    db = _FakeDB(reserved=reserved)
    existing = {"ref_price": 500.0, "is_seed": True}

    row = pricing.recompute_model_price(db, "rtx_4070", existing)

    assert row is not None
    assert row["raw_ref"] == pytest.approx(300.0)
    assert row["shrunk"] is True
    # n_eff is the *trimmed* pool's weight — trimming drops k from each end
    # first (k=1 here, per the dead-zone fix), so only 3 of the 5 comps feed it.
    k = pricing._trim_k(config.MIN_COMPS, config.TRIM_FRACTION)
    n_eff = (config.MIN_COMPS - 2 * k) * config.RESERVED_WEIGHT
    expected = (n_eff * 300.0 + config.PRIOR_WEIGHT * 500.0) / (n_eff + config.PRIOR_WEIGHT)
    assert row["ref_price"] == pytest.approx(round(expected, 2))
    # pulled toward the prior: closer to 500 than to the raw 300 observation
    assert abs(500.0 - row["ref_price"]) < abs(300.0 - row["ref_price"])


def test_shrinkage_barely_moves_the_reference_with_many_comps(monkeypatch):
    """With a healthy sample, the same prior should have little pull — the
    reference should sit close to what was actually observed, not the stale
    seed."""
    monkeypatch.setattr(pricing, "_time_decay", lambda age: 1.0)
    n = 60
    reserved = [_reserved_row(f"r{i}", 300.0) for i in range(n)]
    db = _FakeDB(reserved=reserved)
    existing = {"ref_price": 500.0, "is_seed": True}

    row = pricing.recompute_model_price(db, "rtx_4070", existing)

    assert row is not None
    assert row["raw_ref"] == pytest.approx(300.0)
    k = pricing._trim_k(n, config.TRIM_FRACTION)
    n_eff = (n - 2 * k) * config.RESERVED_WEIGHT
    expected = (n_eff * 300.0 + config.PRIOR_WEIGHT * 500.0) / (n_eff + config.PRIOR_WEIGHT)
    assert row["ref_price"] == pytest.approx(round(expected, 2))
    assert abs(row["ref_price"] - 300.0) < 30.0    # close to the real observation
    assert abs(row["ref_price"] - 500.0) > 150.0   # nowhere near the stale prior


def test_no_prior_available_leaves_ref_price_unshrunk(monkeypatch):
    monkeypatch.setattr(pricing, "_time_decay", lambda age: 1.0)
    reserved = [_reserved_row(f"r{i}", 300.0) for i in range(config.MIN_COMPS)]
    db = _FakeDB(reserved=reserved)  # no existing row, no siblings for a leaf SKU

    row = pricing.recompute_model_price(db, "rtx_4070", None)

    assert row is not None
    assert row["shrunk"] is False
    assert row["ref_price"] == row["raw_ref"] == pytest.approx(300.0)


# --------------------------------------------------------------- provenance
def test_recompute_model_price_records_provenance(monkeypatch):
    monkeypatch.setattr(pricing, "_time_decay", lambda age: 1.0)
    reserved = [_reserved_row(f"r{i}", 300.0) for i in range(4)]
    sold = [_sold_row(f"s{i}", 305.0) for i in range(3)]
    db = _FakeDB(reserved=reserved, sold=sold)

    row = pricing.recompute_model_price(db, "rtx_4070", None)

    assert row is not None
    assert row["n_sold"] == 3
    assert row["n_reserved"] == 4
    assert row["n_comps"] == 7
    assert row["raw_ref"] is not None
    assert row["shrunk"] is False  # no existing ref_price and no sibling prior for a leaf SKU


def test_recompute_model_price_below_min_comps_writes_nothing():
    db = _FakeDB(reserved=[_reserved_row("only-one", 300.0)])
    assert pricing.recompute_model_price(db, "rtx_4070", None) is None
    assert db.writes == []


# ----------------------------------------------------------- time to sale
def test_time_to_sale_days_hand_computed():
    now = pricing.now()
    rows = [
        {"closed_at": now.isoformat(), "posted_at": (now - timedelta(days=d)).isoformat()}
        for d in (2, 3, 4, 5, 6)
    ]
    assert pricing.time_to_sale_days(rows) == pytest.approx(4.0, abs=0.01)  # median of 2,3,4,5,6


def test_time_to_sale_days_falls_back_to_first_seen():
    now = pricing.now()
    rows = [
        {"closed_at": now.isoformat(), "first_seen": (now - timedelta(days=d)).isoformat()}
        for d in (1, 2, 3, 4, 5)
    ]
    assert pricing.time_to_sale_days(rows) == pytest.approx(3.0, abs=0.01)


def test_time_to_sale_days_none_when_not_enough_data():
    now = pricing.now()
    rows = [
        {"closed_at": now.isoformat(), "posted_at": (now - timedelta(days=3)).isoformat()}
        for _ in range(config.MIN_COMPS - 1)
    ]
    assert pricing.time_to_sale_days(rows) is None


def test_time_to_sale_days_ignores_rows_missing_timestamps():
    now = pricing.now()
    rows = [
        {"closed_at": now.isoformat(), "posted_at": (now - timedelta(days=d)).isoformat()}
        for d in (1, 2, 3, 4, 5)
    ]
    rows.append({"closed_at": None, "posted_at": now.isoformat()})  # unusable, must not crash or count
    assert pricing.time_to_sale_days(rows) == pytest.approx(3.0, abs=0.01)


def test_recompute_model_price_uses_supplied_sold_rows_for_time_to_sale(monkeypatch):
    monkeypatch.setattr(pricing, "_time_decay", lambda age: 1.0)
    reserved = [_reserved_row(f"r{i}", 300.0) for i in range(config.MIN_COMPS)]
    db = _FakeDB(reserved=reserved)
    now = pricing.now()
    sold_rows = [
        {"closed_at": now.isoformat(), "posted_at": (now - timedelta(days=d)).isoformat()}
        for d in (2, 3, 4, 5, 6)
    ]

    row = pricing.recompute_model_price(db, "rtx_4070", None, sold_rows=sold_rows)
    assert row["median_days_to_sale"] == pytest.approx(4.0, abs=0.01)


def test_recompute_model_price_carries_forward_median_days_to_sale_when_not_supplied(monkeypatch):
    monkeypatch.setattr(pricing, "_time_decay", lambda age: 1.0)
    reserved = [_reserved_row(f"r{i}", 300.0) for i in range(config.MIN_COMPS)]
    db = _FakeDB(reserved=reserved)
    existing = {"ref_price": 300.0, "median_days_to_sale": 12.5}

    row = pricing.recompute_model_price(db, "rtx_4070", existing)
    assert row["median_days_to_sale"] == pytest.approx(12.5)


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


def test_evaluate_passes_through_provenance_fields():
    """The alert needs to be able to show whether a reference rests on real
    sales or just reservations, and how long the model typically takes to
    move — evaluate() must carry those straight through from model_row."""
    row = {
        "ref_price": 330, "buy_ceiling": 224.0, "buy_ceiling_in_person": 280.0,
        "n_comps": 12, "n_sold": 8, "n_reserved": 4, "median_days_to_sale": 9.5,
    }
    deal = pricing.evaluate(250, row, None)
    assert deal.n_sold == 8
    assert deal.n_reserved == 4
    assert deal.median_days_to_sale == pytest.approx(9.5)


def test_evaluate_defaults_provenance_fields_when_absent():
    """Older/seed rows won't have the new columns populated yet — evaluate()
    must not blow up, and should default sensibly."""
    row = {"ref_price": 330, "buy_ceiling": 224.0, "buy_ceiling_in_person": 280.0, "n_comps": 12}
    deal = pricing.evaluate(250, row, None)
    assert deal.n_sold == 0
    assert deal.n_reserved == 0
    assert deal.median_days_to_sale is None


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
