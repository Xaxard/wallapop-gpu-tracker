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


# ------------------------------------------------------------------- iPhones
@pytest.mark.parametrize(
    "title,key",
    [
        ("iPhone 15 Pro Max 256GB Titanio Negro", "iphone_15_pro_max"),
        ("IPHONE 15 PRO MAX", "iphone_15_pro_max"),
        ("iPhone 15 Pro 128GB Azul Titanio", "iphone_15_pro"),
        ("iPhone 15 Plus 512 GB", "iphone_15_plus"),
        ("iPhone 15 128GB", "iphone_15"),
        ("iPhone 16e 128GB", "iphone_16e"),
        ("iPhone 16 promax 256gb", "iphone_16_pro_max"),
        ("iPhone 17 Pro 256GB Azul Marino", "iphone_17_pro"),
        ("iPhone 17e", "iphone_17e"),
        ("iPhone15 Pro Max 1TB", "iphone_15_pro_max"),
        ("Apple 15 Pro Max", "iphone_15_pro_max"),
    ],
)
def test_iphone_variants_classify(title, key):
    assert models.classify(title).model_key == key


def test_pro_max_is_never_read_as_the_base_model():
    """Registry order is load-bearing: a 15 Pro Max that fell through to
    `iphone_15` would price a ~620 EUR phone against a ~438 EUR one."""
    for gen in ("15", "16", "17"):
        m = models.classify(f"iPhone {gen} Pro Max 256GB")
        assert m.model_key == f"iphone_{gen}_pro_max", gen


def test_iphone_air_outranks_its_generation():
    """Sellers write "iPhone 17 Air", so the Air entry must be tried before the
    bare 17 — caught in testing, where it classified as iphone_17."""
    assert models.classify("iPhone 17 Air 512GB").model_key == "iphone_air"
    assert models.classify("iPhone Air").model_key == "iphone_air"
    assert models.classify("iPhone 17 256GB").model_key == "iphone_17"


def test_iphone_patterns_require_the_brand_token():
    """A bare number is far too loose for phones: `\\b15\\b` alone would swallow
    "Cargador 15W" and any GPU listing quoting a quantity."""
    assert models.classify("Cargador 15W USB-C").model_key is None
    assert models.classify("RTX 4070 15 unidades disponibles").model_key == "rtx_4070"


def test_iphones_older_than_15_are_out_of_scope():
    assert models.classify("iPhone 14 Pro Max 256GB").model_key is None
    assert models.classify("iPhone 13").model_key is None


@pytest.mark.parametrize(
    "title,storage",
    [
        ("iPhone 15 Pro 128GB", "128gb"),
        ("iPhone 15 Pro 256 GB", "256gb"),
        ("iPhone 15 Pro Max 512GB", "512gb"),
        ("iPhone 15 Pro Max 1TB", "1tb"),
        ("iPhone 15 Pro Max", None),
    ],
)
def test_storage_is_extracted(title, storage):
    assert models.classify(title).storage == storage


def test_storage_takes_the_smallest_figure():
    """Opposite of VRAM. Phone titles cross-sell extras ("+ tarjeta 512GB de
    regalo"), and over-reading storage inflates the reference price."""
    m = models.classify("iPhone 15 128GB + funda y tarjeta 512GB de regalo")
    assert m.storage == "128gb"


def test_family_is_tagged_on_every_match():
    assert models.classify("iPhone 15 Pro").family == "phone"
    assert models.classify("RTX 4070 Gigabyte").family == "gpu"
    assert models.family_of("iphone_15_pro") == "phone"
    assert models.family_of("rtx_4070") == "gpu"
    assert models.family_of(None) is None
