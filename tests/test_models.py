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
