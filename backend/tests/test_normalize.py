"""Normalization edge cases (PLAN Phase 1 'done when': kJ->kcal, serving-basis,
label rounding/junk, canonical keys, barcode variants)."""
import pytest

from app.normalize import (
    barcode_variants,
    extract_api_nutrient_rows,
    fdc_api_food,
    finalize_fdc_nutrients,
    off_food,
    off_nutrients,
    slug_nutrient_key,
)

# --- slug_nutrient_key -------------------------------------------------------

@pytest.mark.parametrize("name,unit,expected", [
    ("Magnesium, Mg", "MG", "magnesium_mg_mg"),
    ("Total lipid (fat)", "G", "total_lipid_fat_g"),
    ("Vitamin B-12", "UG", "vitamin_b_12_ug"),
    ("Fatty acids, total saturated", "G", "fatty_acids_total_saturated_g"),
    ("Caffeine", "MG", "caffeine_mg"),
])
def test_slug_nutrient_key(name, unit, expected):
    assert slug_nutrient_key(name, unit) == expected


# --- finalize_fdc_nutrients --------------------------------------------------

def row(nbr, amount, name="X", unit="G"):
    return {"number": nbr, "name": name, "unit": unit, "amount": amount}


def test_canonical_macro_mapping():
    out = finalize_fdc_nutrients([
        row("203", 10.19), row("204", 0.39), row("205", 3.6),
        row("291", 0.0), row("307", 36, unit="MG"), row("208", 59, unit="KCAL"),
    ])
    assert out == {
        "protein_g": 10.19, "fat_g": 0.39, "carbs_g": 3.6,
        "fiber_g": 0.0, "sodium_mg": 36, "kcal": 59,
    }


def test_energy_prefers_explicit_kcal_over_kj():
    out = finalize_fdc_nutrients([row("208", 59), row("268", 247)])
    assert out["kcal"] == 59


def test_energy_derived_from_kj_when_kcal_missing():
    out = finalize_fdc_nutrients([row("268", 247)])
    assert out["kcal"] == pytest.approx(59.0, abs=0.1)  # 247 / 4.184


def test_energy_atwater_fallback_order():
    # Atwater general (957) beats specific (958); both lose to 208.
    assert finalize_fdc_nutrients([row("957", 61), row("958", 62)])["kcal"] == 61
    assert finalize_fdc_nutrients([row("958", 62), row("268", 247)])["kcal"] == 62
    assert finalize_fdc_nutrients([row("208", 59), row("957", 61)])["kcal"] == 59


def test_unknown_nutrient_gets_slugged_key():
    out = finalize_fdc_nutrients([row("337", 3.2, name="Lycopene", unit="UG")])
    assert out == {"lycopene_ug": 3.2}


def test_minerals_map_to_canonical_keys_not_element_slugs():
    out = finalize_fdc_nutrients([
        row("306", 316, name="Potassium, K", unit="MG"),
        row("304", 11, name="Magnesium, Mg", unit="MG"),
        row("221", 0.5, name="Alcohol, ethyl", unit="G"),
    ])
    assert out == {"potassium_mg": 316, "magnesium_mg": 11, "alcohol_g": 0.5}


def test_fdc_and_off_agree_on_keys_for_shared_nutrients():
    # The whole point of FDC_CANONICAL: one key per nutrient across sources.
    fdc = finalize_fdc_nutrients([
        row("306", 316, name="Potassium, K", unit="MG"),
        row("304", 11, name="Magnesium, Mg", unit="MG"),
        row("301", 110, name="Calcium, Ca", unit="MG"),
        row("601", 5, name="Cholesterol", unit="MG"),
        row("606", 0.2, name="Fatty acids, total saturated", unit="G"),
    ])
    off = off_nutrients({
        "potassium_100g": 0.316, "magnesium_100g": 0.011, "calcium_100g": 0.110,
        "cholesterol_100g": 0.005, "saturated-fat_100g": 0.2,
    })
    assert set(fdc) == set(off)
    for key in fdc:
        assert fdc[key] == pytest.approx(off[key], rel=1e-6), key


def test_string_amounts_and_junk_are_tolerated():
    out = finalize_fdc_nutrients([
        row("203", "10.19"),          # labels ship strings
        row("204", ""),               # empty -> dropped
        row("205", None),             # missing -> dropped
        row("307", "n/a", unit="MG"), # junk -> dropped, not crashed
    ])
    assert out == {"protein_g": 10.19}


# --- extract_api_nutrient_rows (both FDC API shapes) --------------------------

def test_extracts_detail_shape():
    payload = {"foodNutrients": [
        {"nutrient": {"number": "203", "name": "Protein", "unitName": "G"}, "amount": 10.2},
    ]}
    assert extract_api_nutrient_rows(payload) == [
        {"number": "203", "name": "Protein", "unit": "G", "amount": 10.2},
    ]


def test_extracts_search_shape():
    payload = {"foodNutrients": [
        {"nutrientNumber": "307", "nutrientName": "Sodium, Na", "unitName": "MG", "value": 36},
    ]}
    assert extract_api_nutrient_rows(payload) == [
        {"number": "307", "name": "Sodium, Na", "unit": "MG", "amount": 36},
    ]


# --- fdc_api_food -------------------------------------------------------------

def test_fdc_detail_food_with_portions():
    item = fdc_api_food({
        "fdcId": 173410, "dataType": "SR Legacy", "description": "Yogurt, Greek, plain, nonfat",
        "foodNutrients": [{"nutrient": {"number": "203", "name": "Protein", "unitName": "G"}, "amount": 10.19}],
        "foodPortions": [
            {"amount": 1, "measureUnit": {"name": "cup"}, "modifier": "", "gramWeight": 245},
            {"portionDescription": "1 container (150g)", "gramWeight": 150},
            {"amount": 1, "measureUnit": {"name": "cup"}, "gramWeight": 0},   # junk weight -> dropped
        ],
    })
    assert item["food"]["source"] == "fdc_sr_legacy"
    assert item["food"]["source_id"] == "173410"
    assert item["portions"] == [
        {"description": "1 cup", "gram_weight": 245},
        {"description": "1 container (150g)", "gram_weight": 150},
    ]
    assert item["nutrients"]["protein_g"] == 10.19


def test_fdc_branded_serving_and_barcode():
    item = fdc_api_food({
        "fdcId": 123, "dataType": "Branded", "description": "GREEK YOGURT",
        "brandOwner": "Chobani", "gtinUpc": "0070734000034",
        "servingSize": 150, "servingSizeUnit": "g",
        "householdServingFullText": "1 container",
        "foodNutrients": [],
    })
    assert item["food"]["source"] == "fdc_branded"
    assert item["food"]["barcode"] == "0070734000034"
    assert item["food"]["brand"] == "Chobani"
    assert item["portions"] == [{"description": "1 container", "gram_weight": 150}]


def test_fdc_branded_ml_serving_uses_1g_per_ml():
    item = fdc_api_food({
        "fdcId": 124, "dataType": "Branded", "description": "SPARKLING WATER",
        "servingSize": 355, "servingSizeUnit": "ml", "foodNutrients": [],
    })
    assert item["portions"] == [{"description": "1 serving (355 ml)", "gram_weight": 355}]


def test_fdc_unsupported_datatype_returns_none():
    assert fdc_api_food({"fdcId": 1, "dataType": "Survey (FNDDS)", "description": "x"}) is None


# --- Open Food Facts ----------------------------------------------------------

def test_off_kcal_preferred_over_kj():
    out = off_nutrients({"energy-kcal_100g": 59, "energy_100g": 247})
    assert out["kcal"] == 59


def test_off_kcal_derived_from_kj():
    out = off_nutrients({"energy_100g": 247})
    assert out["kcal"] == pytest.approx(59.0, abs=0.1)


def test_off_sodium_grams_to_mg():
    assert off_nutrients({"sodium_100g": 0.036})["sodium_mg"] == 36


def test_off_salt_fallback_when_sodium_missing():
    # EU labels: sodium = salt / 2.5
    assert off_nutrients({"salt_100g": 0.09})["sodium_mg"] == 36
    # ...but real sodium wins over the salt derivation
    out = off_nutrients({"salt_100g": 0.09, "sodium_100g": 0.04})
    assert out["sodium_mg"] == 40


def test_off_micros_and_macros():
    out = off_nutrients({
        "proteins_100g": "10.2",      # strings tolerated
        "magnesium_100g": 0.011,      # g -> mg
        "vitamin-b12_100g": 3.8e-7,   # g -> ug
        "saturated-fat_100g": 0.2,
        "unknown-thing_100g": 5,      # not in curated map -> skipped, not guessed
    })
    assert out["protein_g"] == 10.2
    assert out["magnesium_mg"] == 11
    assert out["vitamin_b_12_ug"] == pytest.approx(0.38)
    assert out["fatty_acids_total_saturated_g"] == 0.2
    assert "unknown_thing" not in str(out)


def test_off_food_full_product():
    item = off_food("0070734000034", {
        "product_name": "Plain Greek Yogurt",
        "brands": "Chobani, Some Distributor",
        "serving_quantity": 150, "serving_quantity_unit": "g", "serving_size": "1 container (150g)",
        "nutriments": {"energy-kcal_100g": 59, "proteins_100g": 10.2},
    })
    assert item["food"]["source"] == "off"
    assert item["food"]["barcode"] == "0070734000034"
    assert item["food"]["brand"] == "Chobani"
    assert item["portions"] == [{"description": "1 container (150g)", "gram_weight": 150}]


def test_off_food_unusable_products_return_none():
    assert off_food("123", {"product_name": "X", "nutriments": {}}) is None
    assert off_food("123", {"nutriments": {"energy-kcal_100g": 10}}) is None  # nameless


# --- barcodes -----------------------------------------------------------------

def test_barcode_variants_upc_a():
    # ZXing gives 12-digit UPC-A; OFF/FDC may store stripped or 13/14-padded.
    assert barcode_variants("070734000034") == [
        "070734000034", "70734000034", "0070734000034", "00070734000034",
    ]


def test_barcode_variants_dedup_and_junk():
    assert barcode_variants("9031204141818") == ["9031204141818", "09031204141818"]
    assert barcode_variants("") == []
    assert barcode_variants("abc") == []
    assert barcode_variants("0000") == ["0000", "0", "0000000000000", "00000000000000"]
