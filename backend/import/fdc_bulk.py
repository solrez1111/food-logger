"""Bulk-import FDC Foundation + SR Legacy CSVs into the local food catalog.

Usage (from backend/):
    python import/fdc_bulk.py --zip FoodData_Central_foundation_food_csv_*.zip
    python import/fdc_bulk.py --zip sr_legacy.zip --zip foundation.zip
    python import/fdc_bulk.py --dir /path/to/extracted/csvs

Download the zips (no API key needed) from https://fdc.nal.usda.gov/download-datasets
("Foundation Foods" and "SR Legacy" CSV links). ~15k generic foods total.

Amounts in food_nutrient.csv are already per-100g. source_payload stays NULL
for bulk rows (raw CSV rows add bloat, not information — the API/OFF paths do
keep raw payloads). Re-running is a full idempotent refresh.

NOTE: this directory is named 'import/' per PLAN's layout; that's a Python
keyword, so these are standalone scripts (path shim below), never a package.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

from app.food_db import upsert_food  # noqa: E402
from app.normalize import FDC_DATA_TYPE_TO_SOURCE, finalize_fdc_nutrients  # noqa: E402

BULK_DATA_TYPES = {"foundation_food", "sr_legacy_food"}


def find_csv(root: Path, name: str) -> Path | None:
    hits = sorted(root.rglob(name))
    return hits[0] if hits else None


def read_csv(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def load_dir(root: Path) -> list[dict]:
    """Parse one extracted CSV directory into normalize()-ready items."""
    food_csv = find_csv(root, "food.csv")
    nutrient_csv = find_csv(root, "nutrient.csv")
    food_nutrient_csv = find_csv(root, "food_nutrient.csv")
    if not (food_csv and nutrient_csv and food_nutrient_csv):
        raise SystemExit(f"{root}: missing food.csv / nutrient.csv / food_nutrient.csv")

    foods: dict[str, dict] = {}
    for row in read_csv(food_csv):
        source = FDC_DATA_TYPE_TO_SOURCE.get(row["data_type"])
        if row["data_type"] in BULK_DATA_TYPES and source:
            foods[row["fdc_id"]] = {
                "source": source,
                "source_id": row["fdc_id"],
                "name": row["description"].strip(),
                "brand": None,
                "barcode": None,
            }

    nutrient_meta = {
        row["id"]: {"number": row["nutrient_nbr"], "name": row["name"], "unit": row["unit_name"]}
        for row in read_csv(nutrient_csv)
    }

    nutrient_rows: dict[str, list[dict]] = defaultdict(list)
    for row in read_csv(food_nutrient_csv):
        fdc_id = row["fdc_id"]
        if fdc_id not in foods:
            continue
        meta = nutrient_meta.get(row["nutrient_id"])
        if meta is None:
            continue
        nutrient_rows[fdc_id].append({**meta, "amount": row["amount"]})

    measure_units = {}
    mu_csv = find_csv(root, "measure_unit.csv")
    if mu_csv:
        measure_units = {row["id"]: row["name"] for row in read_csv(mu_csv)}

    portions: dict[str, list[dict]] = defaultdict(list)
    fp_csv = find_csv(root, "food_portion.csv")
    if fp_csv:
        for row in read_csv(fp_csv):
            fdc_id = row["fdc_id"]
            if fdc_id not in foods:
                continue
            try:
                gram_weight = float(row["gram_weight"])
            except (KeyError, ValueError):
                continue
            if gram_weight <= 0:
                continue
            desc = (row.get("portion_description") or "").strip()
            if not desc or desc.lower() == "quantity not specified":
                unit = measure_units.get(row.get("measure_unit_id") or "", "")
                if unit.lower() == "undetermined":
                    unit = ""
                amount = (row.get("amount") or "").strip()
                modifier = (row.get("modifier") or "").strip()
                desc = " ".join(x for x in (amount, unit) if x)
                if modifier:
                    desc = f"{desc}, {modifier}" if desc else modifier
            if desc:
                portions[fdc_id].append({"description": desc, "gram_weight": gram_weight})

    return [
        {
            "food": food,
            "portions": portions.get(fdc_id, []),
            "nutrients": finalize_fdc_nutrients(nutrient_rows.get(fdc_id, [])),
        }
        for fdc_id, food in foods.items()
    ]


async def import_items(items: list[dict]) -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set")
    conn = await asyncpg.connect(url)
    try:
        for i, item in enumerate(items, 1):
            await upsert_food(conn, item, keep_raw=False)
            if i % 500 == 0 or i == len(items):
                print(f"  upserted {i}/{len(items)}")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zip", action="append", default=[], help="FDC CSV zip (repeatable)")
    ap.add_argument("--dir", action="append", default=[], help="extracted CSV directory (repeatable)")
    args = ap.parse_args()
    if not args.zip and not args.dir:
        ap.error("provide --zip and/or --dir")

    all_items: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        roots = [Path(d) for d in args.dir]
        for i, z in enumerate(args.zip):
            dest = Path(tmp) / str(i)
            print(f"extracting {z}")
            with zipfile.ZipFile(z) as zf:
                zf.extractall(dest)
            roots.append(dest)
        for root in roots:
            items = load_dir(root)
            print(f"{root}: {len(items)} foods parsed")
            all_items.extend(items)

        asyncio.run(import_items(all_items))
    print(f"done: {len(all_items)} foods imported/refreshed")


if __name__ == "__main__":
    main()
