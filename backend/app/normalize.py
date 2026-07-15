"""Pure nutrient/portion normalization — no I/O, tested hard (see PLAN Phase 1).

Everything here converts messy upstream shapes (FDC CSV rows, FDC API payloads,
Open Food Facts products) into one internal form:

    food:      {source, source_id, name, brand, barcode}
    portions:  [{description, gram_weight}]
    nutrients: {snake_key: amount_per_100g}

Canonical macro keys are stable because rollups and targets depend on them:
kcal, protein_g, carbs_g, fat_g, fiber_g, sodium_mg. Every other nutrient is
kept under an auto-slugged key like magnesium_mg / vitamin_b_12_ug.

Conservative-normalization rule (PLAN working agreement): when a value can't be
normalized confidently, drop it and keep the raw payload — never guess silently.
"""
from __future__ import annotations

import re

KJ_PER_KCAL = 4.184

# FDC nutrient numbers -> canonical keys. Energy has its own precedence logic
# below (208 direct kcal > 957/958 Atwater > derived from 268 kJ).
#
# The mineral/vitamin entries exist for CROSS-SOURCE consistency: FDC names
# carry element symbols ("Potassium, K") which auto-slug to potassium_k_mg,
# while OFF imports produce potassium_mg — same nutrient, two keys, silently
# split totals. One canonical key per nutrient, whatever the source.
# (Data loaded before this map existed is renamed by migration 0002.)
FDC_CANONICAL = {
    "203": "protein_g",
    "204": "fat_g",
    "205": "carbs_g",
    "291": "fiber_g",
    "307": "sodium_mg",
    # minerals
    "301": "calcium_mg",
    "303": "iron_mg",
    "304": "magnesium_mg",
    "305": "phosphorus_mg",
    "306": "potassium_mg",
    "309": "zinc_mg",
    "312": "copper_mg",
    "315": "manganese_mg",
    "317": "selenium_ug",
    # vitamins
    "320": "vitamin_a_ug",
    "323": "vitamin_e_mg",
    "328": "vitamin_d_ug",
    "401": "vitamin_c_mg",
    "418": "vitamin_b_12_ug",
    # lipids / sugars / stimulants
    "601": "cholesterol_mg",
    "605": "fatty_acids_total_trans_g",
    "606": "fatty_acids_total_saturated_g",
    "269": "sugars_total_g",
    "539": "sugars_added_g",
    "262": "caffeine_mg",
    # dashboard compatibility: nutrition_days.alcohol_g reads this key (Phase 7)
    "221": "alcohol_g",
}
FDC_ENERGY_KCAL = "208"
FDC_ENERGY_KJ = "268"
FDC_ENERGY_ATWATER_GENERAL = "957"
FDC_ENERGY_ATWATER_SPECIFIC = "958"
_ENERGY_NBRS = {FDC_ENERGY_KCAL, FDC_ENERGY_KJ, FDC_ENERGY_ATWATER_GENERAL, FDC_ENERGY_ATWATER_SPECIFIC}


def slug_nutrient_key(name: str, unit: str) -> str:
    """'Vitamin B-12' + 'UG' -> 'vitamin_b_12_ug'; 'Total lipid (fat)' + 'G' -> 'total_lipid_fat_g'."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    unit_slug = re.sub(r"[^a-z0-9]+", "_", unit.lower()).strip("_")
    return f"{base}_{unit_slug}" if unit_slug else base


def _num(value) -> float | None:
    """Tolerant numeric parse — labels and OFF ship strings, '', None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f


def finalize_fdc_nutrients(rows: list[dict]) -> dict[str, float]:
    """rows: [{number, name, unit, amount}] with amounts already per-100g
    (true for FDC Foundation / SR Legacy / Branded foodNutrients).

    Returns {snake_key: amount}. Energy precedence: explicit kcal (208), then
    Atwater general (957), then Atwater specific (958), then kJ/4.184 (268).
    """
    out: dict[str, float] = {}
    energy: dict[str, float] = {}
    for row in rows:
        amount = _num(row.get("amount"))
        if amount is None:
            continue
        nbr = str(row.get("number") or "")
        if nbr in _ENERGY_NBRS:
            energy[nbr] = amount
            continue
        key = FDC_CANONICAL.get(nbr) or slug_nutrient_key(row.get("name") or f"nutrient_{nbr}", row.get("unit") or "")
        out[key] = amount

    for nbr in (FDC_ENERGY_KCAL, FDC_ENERGY_ATWATER_GENERAL, FDC_ENERGY_ATWATER_SPECIFIC):
        if nbr in energy:
            out["kcal"] = energy[nbr]
            break
    else:
        if FDC_ENERGY_KJ in energy:
            out["kcal"] = round(energy[FDC_ENERGY_KJ] / KJ_PER_KCAL, 1)
    return out


def extract_api_nutrient_rows(payload: dict) -> list[dict]:
    """Normalize both FDC API shapes to finalize_fdc_nutrients() input.

    Detail endpoint:  foodNutrients: [{nutrient: {number, name, unitName}, amount}]
    Search results:   foodNutrients: [{nutrientNumber, nutrientName, unitName, value}]
    """
    rows = []
    for fn in payload.get("foodNutrients") or []:
        if "nutrient" in fn:
            n = fn["nutrient"]
            rows.append({
                "number": n.get("number"),
                "name": n.get("name"),
                "unit": n.get("unitName"),
                "amount": fn.get("amount"),
            })
        else:
            rows.append({
                "number": fn.get("nutrientNumber"),
                "name": fn.get("nutrientName"),
                "unit": fn.get("unitName"),
                "amount": fn.get("value"),
            })
    return rows


FDC_DATA_TYPE_TO_SOURCE = {
    "foundation_food": "fdc_foundation",
    "sr_legacy_food": "fdc_sr_legacy",
    "Foundation": "fdc_foundation",
    "SR Legacy": "fdc_sr_legacy",
    "Branded": "fdc_branded",
    "branded_food": "fdc_branded",
}


def fdc_api_food(payload: dict) -> dict | None:
    """FDC API food payload (detail or search hit) -> (food, portions, nutrients) dict.

    Returns None for data types we don't import (survey/experimental foods).
    """
    source = FDC_DATA_TYPE_TO_SOURCE.get(payload.get("dataType") or "")
    if source is None:
        return None

    barcode = (payload.get("gtinUpc") or "").strip() or None
    food = {
        "source": source,
        "source_id": str(payload["fdcId"]),
        "name": (payload.get("description") or "").strip(),
        "brand": (payload.get("brandOwner") or payload.get("brandName") or "").strip() or None,
        "barcode": barcode,
    }

    portions = []
    for p in payload.get("foodPortions") or []:
        gw = _num(p.get("gramWeight"))
        if gw is None or gw <= 0:
            continue
        desc = (p.get("portionDescription") or "").strip()
        if not desc or desc.lower() == "quantity not specified":
            amount = _num(p.get("amount"))
            unit = ((p.get("measureUnit") or {}).get("name") or "").strip()
            modifier = (p.get("modifier") or "").strip()
            if unit.lower() == "undetermined":
                unit = ""
            parts = []
            if amount is not None:
                parts.append(f"{amount:g}")
            if unit:
                parts.append(unit)
            desc = " ".join(parts)
            if modifier:
                desc = f"{desc}, {modifier}" if desc else modifier
        if desc:
            portions.append({"description": desc, "gram_weight": gw})

    # Branded foods carry a label serving instead of foodPortions. servingSizeUnit
    # 'ml' is imported at 1 g/ml — the standard label-data assumption; flagged
    # here rather than guessed silently (density varies, but label nutrition is
    # per-100g/100ml on the same basis, so self-consistency holds).
    serving = _num(payload.get("servingSize"))
    unit = (payload.get("servingSizeUnit") or "").strip().lower()
    if serving and serving > 0 and unit in ("g", "grm", "gram", "ml", "mlt"):
        household = (payload.get("householdServingFullText") or "").strip()
        desc = household or f"1 serving ({serving:g} {'ml' if unit.startswith('ml') else 'g'})"
        portions.append({"description": desc, "gram_weight": serving})

    return {
        "food": food,
        "portions": portions,
        "nutrients": finalize_fdc_nutrients(extract_api_nutrient_rows(payload)),
    }


# --- Open Food Facts ---------------------------------------------------------

# OFF nutriments *_100g values are normalized by OFF to grams (energy in kJ).
# Curated conversions to our per-100g keys; unknown keys are skipped, never
# guessed (their raw stays in source_payload).
OFF_CANONICAL = {
    "proteins": ("protein_g", 1),
    "carbohydrates": ("carbs_g", 1),
    "fat": ("fat_g", 1),
    "fiber": ("fiber_g", 1),
    "sodium": ("sodium_mg", 1000),  # g -> mg
}
OFF_MICROS = {
    # OFF key: (our key, multiplier from grams)
    "salt": None,  # handled via sodium fallback below
    "calcium": ("calcium_mg", 1000),
    "iron": ("iron_mg", 1000),
    "potassium": ("potassium_mg", 1000),
    "magnesium": ("magnesium_mg", 1000),
    "zinc": ("zinc_mg", 1000),
    "phosphorus": ("phosphorus_mg", 1000),
    "cholesterol": ("cholesterol_mg", 1000),
    "saturated-fat": ("fatty_acids_total_saturated_g", 1),
    "trans-fat": ("fatty_acids_total_trans_g", 1),
    "monounsaturated-fat": ("fatty_acids_total_monounsaturated_g", 1),
    "polyunsaturated-fat": ("fatty_acids_total_polyunsaturated_g", 1),
    "sugars": ("sugars_total_g", 1),
    "vitamin-c": ("vitamin_c_mg", 1000),
    "vitamin-a": ("vitamin_a_ug", 1_000_000),
    "vitamin-d": ("vitamin_d_ug", 1_000_000),
    "vitamin-b12": ("vitamin_b_12_ug", 1_000_000),
    "caffeine": ("caffeine_mg", 1000),
    "alcohol": ("alcohol_g", 1),  # OFF reports % vol as g/100g for alcohol key
}


def off_nutrients(nutriments: dict) -> dict[str, float]:
    """OFF product.nutriments -> {snake_key: amount_per_100g}.

    Prefers *_100g fields. Energy: energy-kcal_100g wins; energy_100g is kJ and
    gets divided. Sodium falls back to salt/2.5 (EU labels list salt only).
    """
    out: dict[str, float] = {}

    for off_key, spec in {**OFF_CANONICAL, **{k: v for k, v in OFF_MICROS.items() if v}}.items():
        val = _num(nutriments.get(f"{off_key}_100g"))
        if val is None:
            continue
        key, mult = spec
        out[key] = round(val * mult, 4)

    kcal = _num(nutriments.get("energy-kcal_100g"))
    if kcal is not None:
        out["kcal"] = kcal
    else:
        kj = _num(nutriments.get("energy_100g"))
        if kj is not None:
            out["kcal"] = round(kj / KJ_PER_KCAL, 1)

    if "sodium_mg" not in out:
        salt_g = _num(nutriments.get("salt_100g"))
        if salt_g is not None:
            out["sodium_mg"] = round(salt_g / 2.5 * 1000, 1)

    return out


def off_food(code: str, product: dict) -> dict | None:
    """OFF product payload -> (food, portions, nutrients), or None if unusable.

    Unusable = no per-100g nutriment data at all (serving-only OFF entries
    without serving mass can't be normalized — conservative rule: skip).
    """
    nutrients = off_nutrients(product.get("nutriments") or {})
    if not nutrients:
        return None

    name = (product.get("product_name") or "").strip()
    if not name:
        return None

    portions = []
    qty = _num(product.get("serving_quantity"))
    unit = (product.get("serving_quantity_unit") or "g").strip().lower()
    if qty and qty > 0 and unit in ("g", "ml"):  # ml at 1 g/ml, same flag as FDC branded
        desc = (product.get("serving_size") or "").strip() or f"1 serving ({qty:g} {unit})"
        portions.append({"description": desc, "gram_weight": qty})

    return {
        "food": {
            "source": "off",
            "source_id": str(code),
            "name": name,
            "brand": (product.get("brands") or "").split(",")[0].strip() or None,
            "barcode": str(code),
        },
        "portions": portions,
        "nutrients": nutrients,
    }


# --- Barcodes ----------------------------------------------------------------

def barcode_variants(code: str) -> list[str]:
    """Lookup variants for a scanned code, most-likely first.

    UPC-A scans are 12 digits; OFF stores EAN-13 (13, often zero-padded);
    FDC gtinUpc strings vary in leading zeros. Try: as-scanned, zero-stripped,
    padded to 13 and 14.
    """
    digits = re.sub(r"\D", "", code or "")
    if not digits:
        return []
    variants = [digits, digits.lstrip("0") or "0", digits.zfill(13), digits.zfill(14)]
    seen: list[str] = []
    for v in variants:
        if v not in seen:
            seen.append(v)
    return seen
