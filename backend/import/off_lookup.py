"""Resolve a barcode through the full chain: local DB -> OFF -> FDC branded.

Usage (from backend/):
    python import/off_lookup.py --barcode 070734000034

Upstream hits are cached into the local catalog, so the second lookup of the
same code never leaves the database.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402
import httpx  # noqa: E402

from app.food_sources import resolve_barcode  # noqa: E402


async def run(code: str) -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set")
    conn = await asyncpg.connect(url)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            detail = await resolve_barcode(conn, client, code)
    finally:
        await conn.close()

    if detail is None:
        print(f"no match for {code} (local, OFF, FDC branded all missed)")
        return
    food, n = detail["food"], detail["nutrients"]
    print(f"[{food['source']}] {food['name']}" + (f" — {food['brand']}" if food.get("brand") else ""))
    print(f"  per 100g: kcal={n.get('kcal')} protein_g={n.get('protein_g')} "
          f"carbs_g={n.get('carbs_g')} fat_g={n.get('fat_g')} sodium_mg={n.get('sodium_mg')}")
    for p in detail["portions"]:
        print(f"  portion: {p['description']} = {p['gram_weight']}g")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--barcode", required=True)
    asyncio.run(run(ap.parse_args().barcode))


if __name__ == "__main__":
    main()
