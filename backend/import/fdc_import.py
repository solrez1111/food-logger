"""Import individual foods from the FDC API (needs FDC_API_KEY).

Usage (from backend/):
    python import/fdc_import.py --fdc-id 173410
    python import/fdc_import.py --search "greek yogurt"            # list hits
    python import/fdc_import.py --search "greek yogurt" --import-all
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

from app.food_db import upsert_food  # noqa: E402
from app.food_sources import fdc_get_food, fdc_search  # noqa: E402
from app.normalize import fdc_api_food  # noqa: E402


async def run(args: argparse.Namespace) -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set")
    conn = await asyncpg.connect(url)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if args.fdc_id:
                payload = await fdc_get_food(client, args.fdc_id)
                item = fdc_api_food(payload)
                if item is None:
                    raise SystemExit(f"unsupported dataType: {payload.get('dataType')}")
                item["raw"] = payload
                food_id = await upsert_food(conn, item)
                n = item["nutrients"]
                print(f"imported food id={food_id}: {item['food']['name']} "
                      f"(kcal={n.get('kcal')}, protein_g={n.get('protein_g')}, sodium_mg={n.get('sodium_mg')})")
                return

            hits = await fdc_search(client, args.search, page_size=args.page_size)
            if not hits:
                print("no results")
                return
            for payload in hits:
                line = f"{payload['fdcId']:>8}  [{payload.get('dataType')}] {payload.get('description')}"
                if payload.get("brandOwner"):
                    line += f" — {payload['brandOwner']}"
                print(line)
                if args.import_all:
                    item = fdc_api_food(payload)
                    if item:
                        item["raw"] = payload
                        await upsert_food(conn, item)
            if args.import_all:
                print("imported all supported hits")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fdc-id")
    g.add_argument("--search")
    ap.add_argument("--import-all", action="store_true", help="with --search: import every supported hit")
    ap.add_argument("--page-size", type=int, default=10)
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
