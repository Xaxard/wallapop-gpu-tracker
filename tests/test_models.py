"""Parser tests against the kind of titles Wallapop actually carries."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import models  # noqa: E402


@pytest.mark.parametrize(
    "title,expected",
    [
        # --- straightforward -------------------------------------------------
        ("RTX 3070 Gigabyte Gaming OC", "rtx_3070"),
        ("Nvidia GeForce RTX 4070 MSI Ventus 3X", "rtx_4070"),
        ("Tarjeta grafica RX 7800 XT Sapphire Pulse", "rx_7800_xt"),
        # --- the specificity trap: base must not swallow the variants --------
        ("RTX 4070 Ti Super 16GB Asus TUF", "rtx_4070_ti_super"),
        ("RTX 4070 TI Gaming X Trio", "rtx_4070_ti"),
        ("Rtx 4070 Super Palit JetStream", "rtx_4070_super"),
        ("RTX 3080 Ti FE 12GB", "rtx_3080_ti"),
        ("RTX 3070 Ti Zotac", "rtx_3070_ti"),
        # --- squashed / punctuated spellings ---------------------------------
        ("RTX4070ti gaming", "rtx_4070_ti"),
        ("rtx-3070 evga ftw3", "rtx_3070"),
        ("GRÁFICA RTX 4060 nueva", "rtx_4060"),
        # --- VRAM disambiguation ---------------------------------------------
        ("RTX 4060 Ti 16GB Gigabyte Eagle", "rtx_4060_ti_16g"),
        ("RTX 4060 Ti 8 GB Asus Dual", "rtx_4060_ti_8g"),
        ("RTX 4060 Ti Zotac Twin Edge", "rtx_4060_ti"),
        ("RTX 5060 Ti 16 gb MSI", "rtx_5060_ti_16g"),
        # --- AMD --------------------------------------------------------------
        ("AMD Radeon RX 9060 XT 16GB", "rx_9060_xt_16g"),
        ("RX 9060 XT Powercolor Reaper", "rx_9060_xt"),
        ("RX 7900 XTX Nitro+", "rx_7900_xtx"),
        ("RX 7900 XT Red Devil", "rx_7900_xt"),
        ("Radeon RX 7900 GRE Hellhound", "rx_7900_gre"),
        ("RX 7600 8GB XFX", "rx_7600"),
        # --- no GPU in the title ---------------------------------------------
        ("Google Pixel 8 Pro 256GB", None),
        ("Fuente de alimentación Corsair 750W", None),
        ("Monitor 27 pulgadas 144hz", None),
    ],
)
def test_classify(title, expected):
    assert models.classify(title).model_key == expected


def test_bare_number_is_low_confidence():
    """No brand token means we must not let it drive the margin engine."""
    match = models.classify("Vendo 4070 poco uso")
    assert match.model_key == "rtx_4070"
    assert match.confidence == "low"
    assert not match.priceable


def test_brand_token_lifts_confidence():
    match = models.classify("RTX 4070 poco uso")
    assert match.confidence == "high"
    assert match.priceable


def test_amd_grants_high_confidence_even_though_it_also_names_cpus():
    """'amd' stays a brand token (recall over precision — real AMD GPU
    listings don't always say "rx"/"radeon" literally). The Ryzen-vs-Radeon
    number collision (Ryzen 5 7600 vs RX 7600) is instead guarded by junk.py's
    CPU_TOKENS filter, not by stripping 'amd' from the confidence check here.
    """
    match = models.classify("Procesador AMD Ryzen 5 7600 6 nucleos 12 hilos")
    assert match.model_key == "rx_7600"
    assert match.confidence == "high"  # junk.check() is what stops this listing, not classify()


def test_vram_from_description_only():
    match = models.classify("RTX 4060 Ti Asus Dual OC", "Tarjeta con 16 GB de memoria GDDR6")
    assert match.model_key == "rtx_4060_ti_16g"


def test_pc_bundle_does_not_become_a_4070_ti():
    """A whole-PC listing still classifies by its GPU, not by the RAM figure."""
    match = models.classify("PC Gaming Ryzen 5 + RTX 4070 + 32GB RAM")
    assert match.model_key == "rtx_4070"


def test_normalise():
    assert models.normalise("GRÁFICA  RTX-4070!!") == "grafica rtx 4070"
    assert models.normalise("4060ti") == "4060 ti"
    assert models.normalise(None) == ""


def test_every_registry_key_is_unique():
    keys = [m.key for m in models.REGISTRY]
    assert len(keys) == len(set(keys))


def test_every_model_has_a_seed_price():
    """A model with an alert search but no seed price falls through to the
    bootstrap-cap branch and fires a bare "matches your search" alert with no
    margin analysis — exactly the noise the margin engine replaces. Caught in
    production the first time RX 6000 searches were added without prices."""
    import seed

    registry = {m.key for m in models.REGISTRY}
    priced = set(seed.SEED_PRICES)
    assert registry - priced == set(), f"no seed price for: {sorted(registry - priced)}"
    assert priced - registry == set(), f"seed price for unknown model: {sorted(priced - registry)}"


def test_every_alert_search_targets_a_priced_model():
    import seed

    priced = set(seed.SEED_PRICES)
    unpriced = [key for _, _, key, _ in seed.ALERT_SEARCHES if key not in priced]
    assert not unpriced, f"alert searches with no reference price: {unpriced}"


# ------------------------------------------------- cross-vendor number collisions
def test_vintage_geforce_7600_is_not_priced_as_a_radeon_rx_7600():
    """AMD's RX 7600 is 2023; NVIDIA's GeForce 7600 GS is 2006. A real listing,
    "PC de escritorio HP P4 + Nvidia 7600GS" at 60 EUR, used to classify as a
    high-confidence RX 7600 and report a 143 EUR margin on a twenty-year-old
    card. The number still matches — but nvidia branding must not vouch for an
    AMD key, so it drops below priceable and can never reach the margin engine.
    """
    match = models.classify("PC de escritorio HP P4 + Nvidia 7600GS")
    assert match.model_key == "rx_7600"
    assert match.confidence == "low"
    assert not match.priceable


def test_real_radeon_still_classifies_high():
    for title in ("Tarjeta Grafica AMD Radeon RX 7600 8GB", "Grafica RX 6600 8GB"):
        match = models.classify(title)
        assert match.priceable, title


def test_own_brand_token_outranks_a_rival_mention():
    """Sellers write sloppy titles and legitimately name both vendors ("cambio
    mi RX 7600 por una Nvidia"). A rival token only downgrades when there is no
    own-brand evidence at all — it never overrides a definitive one."""
    assert models.classify("Radeon RTX 4070").priceable
    assert models.classify("cambio mi RX 7600 por una Nvidia").priceable


def test_generic_card_words_still_vouch_for_either_vendor():
    """"grafica"/"gpu"/"vga" make no vendor claim, so they keep working as
    proof for both sides."""
    assert models.classify("Tarjeta grafica 4070 12GB").priceable
    assert models.classify("Tarjeta grafica 7600 8GB").priceable


# ------------------------------------------- system RAM must not be read as VRAM
@pytest.mark.parametrize(
    "title,expected",
    [
        # The motivating shape: an 8GB card in a machine with 16GB of system
        # RAM. Largest-wins classified this as the 16GB SKU and priced an 8GB
        # card against a reference a whole tier higher — a false bargain
        # manufactured out of a correctly-priced listing.
        ("RTX 4060 Ti 8GB — PC con 16GB RAM", "rtx_4060_ti_8g"),
        # Same, with the irrelevant figure written first, so the fix cannot be
        # "prefer the earliest number" by accident.
        ("PC con 16GB RAM y RTX 4060 Ti 8GB", "rtx_4060_ti_8g"),
        # And the case largest-wins got right for the wrong reason: the card is
        # the 16GB one and the RAM figure is bigger still. Under largest-wins the
        # 32 matched no VRAM-gated entry at all, so this silently degraded to the
        # generic key and lost the 16GB pool.
        ("RTX 4060 Ti 16GB Gigabyte, PC 32GB RAM", "rtx_4060_ti_16g"),
        ("Sapphire RX 9060 XT 8GB, saco de PC con 32 GB de RAM", "rx_9060_xt_8g"),
    ],
)
def test_vram_nearest_the_model_number_wins_over_system_ram(title, expected):
    assert models.classify(title).model_key == expected


def test_extract_vram_still_answers_largest_wins_without_a_position():
    """The bare signature is the description path and is unchanged: with no
    model number to be near, there is no positional information to use."""
    assert models.extract_vram("rtx 4060 ti 8 gb pc con 16 gb ram") == 16
    assert models.extract_vram("sin memoria declarada") is None


def test_extract_vram_near_a_span_picks_the_adjacent_figure():
    norm = models.normalise("RTX 4060 Ti 8GB — PC con 16GB RAM")
    span = (norm.index("4060"), norm.index("4060") + len("4060 ti"))
    assert models.extract_vram(norm, near=span) == 8
    assert models.extract_vram(norm) == 16  # same string, no position -> old answer


def test_vram_proximity_does_not_disturb_the_pc_bundle_case():
    """Regression guard on the existing bundle test: a whole-PC title still
    classifies by its GPU and the RAM figure still isn't mistaken for VRAM."""
    match = models.classify("PC Gaming Ryzen 5 + RTX 4070 + 32GB RAM")
    assert match.model_key == "rtx_4070"


# ------------------------------------------------- model named in the description
def test_model_named_only_in_the_description_is_recognised_but_not_priced():
    """A vague title still gets its model recognised, so the listing is visible
    in logs and to same_family(). It is deliberately NOT priceable.

    This branch shipped priceable and the first live run produced three alerts,
    all wrong, all from here — a GTX 1660 priced as an RTX 3060, a Samsung TV as
    an RTX 5080, an NVLink bridge as an RTX 3090. A description naming one card
    is not evidence the listing is that card.
    """
    match = models.classify(
        "Tarjeta grafica Nvidia en perfecto estado",
        "Es una RTX 4070 Ti de MSI, comprada en 2024, con caja",
    )
    assert match.model_key == "rtx_4070_ti"
    assert match.confidence == "low"
    assert not match.priceable


def test_a_description_match_is_capped_at_low_confidence():
    """Even with unambiguous branding in the description, a description match
    stays below priceable. Strong branding is what made the live false positives
    convincing, not what made them correct."""
    match = models.classify(
        "Componente de ordenador, en buen estado",
        "Nvidia GeForce RTX 4070 Super, tarjeta grafica de 12 GB",
    )
    assert match.model_key == "rtx_4070_super"
    assert match.confidence == "low"
    assert not match.priceable


def test_a_title_match_always_beats_the_description():
    """The description is consulted only when the title matched *nothing*. A
    seller listing a 3060 who mentions a 4090 elsewhere sells a 3060."""
    match = models.classify(
        "RTX 3060 12GB Asus Dual",
        "Tambien vendo una RTX 4090 y una RX 7900 XTX, pregunta",
    )
    assert match.model_key == "rtx_3060_12g"
    assert match.confidence == "high"


def test_description_naming_several_models_is_not_priceable():
    """A description listing several cards is a bundle, a parts list or a
    "compatible con" blurb, and there is no way to tell which card is for sale.
    Ambiguity costs the match its priceability rather than resolving to the most
    expensive candidate — the old first-registry-hit rule always picked the
    highest tier named, so it was systematically biased toward inventing
    bargains. Strong brand tokens must not buy back the confidence.
    """
    match = models.classify(
        "Lote de componentes de PC",
        "Incluye una RX 6600, una GTX 1650 y una Nvidia RTX 4070 Ti",
    )
    assert match.confidence == "low"
    assert not match.priceable


def test_the_adjacent_tier_false_positive_shape_is_unpriceable():
    """The concrete shape this cap exists for, and the one MIN_PLAUSIBLE_RATIO
    does not catch. A 3060 for sale whose description also mentions a 4090 used
    to classify as a medium-confidence 4090 (registry order picks the top tier),
    price a 300 EUR card against the 4090 reference, and alert as a huge margin.
    Gross mismatches are caught by the plausibility floor; adjacent-tier ones —
    a 300 EUR card against a 520 EUR 4070 Ti Super seed, offer 240 against a
    ~392 ceiling — are not, and are the common case.
    """
    match = models.classify(
        "Tarjeta grafica Nvidia, buen estado, envio incluido",
        "La sacaba de mi PC. Nvidia RTX 3060 12GB. Tambien vendo una RTX 4090 aparte.",
    )
    assert match.confidence == "low"
    assert not match.priceable


def test_a_description_naming_one_card_several_ways_still_resolves_to_that_card():
    """People name the same card repeatedly and inconsistently. The key resolved
    must be that one card — it drives logging and same_family() — even though
    nothing from a description is priceable any more."""
    match = models.classify(
        "Componente de ordenador, recogida en Madrid",
        "Vendo RTX 4070. La 4070 esta como nueva, rtx4070 con su caja original.",
    )
    assert match.model_key == "rtx_4070"
    assert not match.priceable


def test_a_split_vram_description_still_resolves_to_the_right_variant():
    """"4060 ti 16 gb" matches the 16GB, 8GB and generic entries at one offset;
    the most specific must win, so the key recorded against the listing is the
    16GB variant rather than the generic pool."""
    match = models.classify(
        "Grafica Nvidia en venta",
        "Es una RTX 4060 Ti de 16 GB, modelo Gigabyte Eagle",
    )
    assert match.model_key == "rtx_4060_ti_16g"
    assert not match.priceable


def test_a_title_match_is_unaffected_by_extra_models_in_the_description():
    """The cap applies only to the description-only branch. A title match stays
    'high' no matter what the description lists — including the pre-existing
    whole-PC bundle behaviour, which must not shift."""
    match = models.classify(
        "PC Gaming Ryzen 5 + RTX 4070 + 32GB RAM",
        "Incluye tambien una RTX 3060 y una RX 6600 de repuesto",
    )
    assert match.model_key == "rtx_4070"
    assert match.confidence == "high"
    assert match.priceable


def test_description_match_with_no_brand_evidence_anywhere_stays_low():
    """The medium cap is a ceiling, not a floor — a bare number in a description
    with no brand token in either field is still unpriceable."""
    match = models.classify("Vendo pieza de ordenador", "es una 4070, poco uso")
    assert match.model_key == "rtx_4070"
    assert match.confidence == "low"
    assert not match.priceable


def test_description_match_reads_brand_evidence_from_the_title_too():
    """The vague titles this branch exists for do usually name the vendor and
    only omit the number, so brand proof may come from either field. It also
    keeps the cross-vendor downgrade working across fields: nvidia branding must
    not vouch for an AMD key even when the number is in the description."""
    match = models.classify("Tarjeta grafica Nvidia GeForce", "modelo 7600, 8 gb")
    assert match.model_key == "rx_7600"
    assert match.confidence == "low"
    assert not match.priceable


def test_description_vram_disambiguates_a_description_only_match():
    match = models.classify(
        "Tarjeta grafica AMD, poco uso",
        "Es una RX 9060 XT de 16 GB, Powercolor Reaper",
    )
    assert match.model_key == "rx_9060_xt_16g"
    assert match.vram == 16


@pytest.mark.parametrize("description", [None, "", "   ", "vendo por no usar, sin caja"])
def test_a_description_naming_no_model_is_still_no_match(description):
    """The new branch must not invent matches: no model in either field is still
    NO_MATCH, unpriceable, and handled by the bootstrap-cap path as before."""
    match = models.classify("Cosa de ordenador en venta", description)
    assert match.model_key is None
    assert match.confidence == "none"
    assert not match.priceable


# ------------------------------------------------------------------ phones
@pytest.mark.parametrize("title,expected", [
    ("iPhone 15 128GB negro", "iphone_15"),
    ("iPhone 15 Plus 256 GB", "iphone_15_plus"),
    ("iPhone 15 Pro 256GB titanio", "iphone_15_pro"),
    ("iPhone 15 Pro Max 1TB", "iphone_15_pro_max"),
    ("iPhone15Pro azul, impecable", "iphone_15_pro"),
    ("Apple iPhone 16 128 GB", "iphone_16"),
    ("iphone16e 128gb", "iphone_16e"),
    ("iPhone 16 Pro Max 512GB", "iphone_16_pro_max"),
    ("iPhone 17 Pro Max nuevo", "iphone_17_pro_max"),
    ("iPhone Air 256GB", "iphone_air"),
])
def test_iphone_classification(title, expected):
    assert models.classify(title).model_key == expected


def test_the_most_specific_iphone_variant_wins():
    """Same rule as the GPU registry: 'pro max' must be tested before 'pro',
    and 'pro' before the bare number, or every Pro Max collapses into the base
    model's pool and drags its median up by hundreds of euros."""
    assert models.classify("iPhone 15 Pro Max").model_key == "iphone_15_pro_max"
    assert models.classify("iPhone 15 Pro").model_key == "iphone_15_pro"
    assert models.classify("iPhone 15").model_key == "iphone_15"


def test_16e_is_not_swallowed_by_the_bare_16_pattern():
    """normalise() splits '16e' into '16 e', so without its own entry above the
    bare-16 pattern the 16e — a much cheaper phone — lands in the 16's pool."""
    assert models.classify("iPhone 16e 128GB").model_key == "iphone_16e"


def test_a_bare_number_is_never_an_iphone():
    """_iph requires the literal word 'iphone'. Two-digit model numbers collide
    with sizes, quantities and years all over marketplace text, so a bare '15
    pro' must not match anything."""
    assert models.classify("Vendo 15 pro unidades").model_key is None
    assert models.classify("15 Pro Max").model_key is None
    assert models.classify("Lote de 16 fundas, 15 pro max incluidas").model_key is None


def test_iphones_carry_the_phone_family_and_gpus_do_not():
    """family is what stops a handset reaching the alert path — see
    alert_loop.ALERTING_FAMILIES."""
    assert models.classify("iPhone 15 Pro").family == "phone"
    assert models.classify("RTX 4070 Gigabyte").family == "gpu"


@pytest.mark.parametrize("title,expected", [
    ("iPhone 15 Pro 128GB", "128gb"),
    ("iPhone 15 Pro Max 1TB", "1tb"),
    ("iPhone 16 256 GB", "256gb"),
    ("iPhone 16 Pro 512gb", "512gb"),
    ("iPhone 15 Pro sin especificar", None),
])
def test_extract_storage(title, expected):
    assert models.extract_storage(title) == expected


def test_every_phone_model_has_a_comps_search():
    """A tracked model with no comps search can never learn a real price and
    stays pinned to its seed forever — the same invariant the GPU side has."""
    import seed

    phones = {m.key for m in models.REGISTRY if m.family == "phone"}
    searched = {k for k, _ in seed.PHONE_COMPS}
    assert phones - searched == set(), f"no comps search for: {sorted(phones - searched)}"
