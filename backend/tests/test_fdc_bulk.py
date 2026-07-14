"""fdc_bulk.load_dir parsing against fixture CSVs shaped like real FDC exports.

The import/ directory is a Python keyword, so the script is loaded by path.
"""
import importlib.util
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "fdc_csv"

spec = importlib.util.spec_from_file_location("fdc_bulk", BACKEND / "import" / "fdc_bulk.py")
fdc_bulk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fdc_bulk)


def by_id(items):
    return {i["food"]["source_id"]: i for i in items}


def test_load_dir_parses_foundation_and_sr_legacy_only():
    items = by_id(fdc_bulk.load_dir(FIXTURES))
    assert set(items) == {"173410", "747997"}  # survey food skipped
    assert items["173410"]["food"]["source"] == "fdc_sr_legacy"
    assert items["747997"]["food"]["source"] == "fdc_foundation"


def test_load_dir_nutrients_with_energy_precedence():
    items = by_id(fdc_bulk.load_dir(FIXTURES))
    yogurt = items["173410"]["nutrients"]
    # explicit kcal (208) wins over the kJ row also present
    assert yogurt["kcal"] == 59
    assert yogurt["protein_g"] == 10.19
    assert yogurt["sodium_mg"] == 36
    assert yogurt["magnesium_mg_mg"] == 11  # non-canonical -> slugged

    broccoli = items["747997"]["nutrients"]
    # only kJ present -> derived kcal
    assert abs(broccoli["kcal"] - 33.7) < 0.1
    assert broccoli["fiber_g"] == 2.6


def test_load_dir_portions():
    items = by_id(fdc_bulk.load_dir(FIXTURES))
    assert items["173410"]["portions"] == [{"description": "1 cup", "gram_weight": 245.0}]
    broccoli = items["747997"]["portions"]
    assert {"description": "1 cup chopped", "gram_weight": 91.0} in broccoli
    assert {"description": "1, floret", "gram_weight": 11.0} in broccoli
    assert len(broccoli) == 2  # zero-gram portion dropped
