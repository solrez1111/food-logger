"""Food catalog persistence — idempotent upserts into the food_log schema.

Upsert key is (source, source_id) per PLAN's idempotency rule; portions and
nutrients are replaced wholesale inside the same transaction so a re-import
never leaves stale child rows.
"""
from __future__ import annotations

import json
from decimal import Decimal

import asyncpg

from .normalize import barcode_variants


def _dec(value) -> Decimal:
    """Insert floats via short-repr Decimal so numeric columns store 2.6,
    not 2.600000000000000088817841970012523233890533447265625."""
    return Decimal(repr(float(value)))

UPSERT_FOOD = """
INSERT INTO food_log.foods (source, source_id, name, brand, barcode, source_payload)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (source, source_id) DO UPDATE SET
  name = EXCLUDED.name,
  brand = EXCLUDED.brand,
  barcode = EXCLUDED.barcode,
  source_payload = COALESCE(EXCLUDED.source_payload, food_log.foods.source_payload),
  updated_at = now()
RETURNING id
"""


async def upsert_food(conn: asyncpg.Connection, item: dict, keep_raw: bool = True) -> int:
    """item: {food, portions, nutrients} from app.normalize. Returns food id."""
    food = item["food"]
    payload = json.dumps(item.get("raw")) if keep_raw and item.get("raw") is not None else None
    async with conn.transaction():
        food_id = await conn.fetchval(
            UPSERT_FOOD,
            food["source"], food["source_id"], food["name"],
            food.get("brand"), food.get("barcode"), payload,
        )
        await conn.execute("DELETE FROM food_log.portions WHERE food_id = $1", food_id)
        if item["portions"]:
            await conn.executemany(
                "INSERT INTO food_log.portions (food_id, description, gram_weight) VALUES ($1, $2, $3)",
                [(food_id, p["description"], _dec(p["gram_weight"])) for p in item["portions"]],
            )
        await conn.execute("DELETE FROM food_log.nutrients WHERE food_id = $1", food_id)
        if item["nutrients"]:
            await conn.executemany(
                "INSERT INTO food_log.nutrients (food_id, nutrient_key, amount_per_100g) VALUES ($1, $2, $3)",
                [(food_id, k, _dec(v)) for k, v in item["nutrients"].items()],
            )
    return food_id


async def find_by_barcode(conn: asyncpg.Connection, code: str) -> asyncpg.Record | None:
    variants = barcode_variants(code)
    if not variants:
        return None
    return await conn.fetchrow(
        """SELECT id, source, source_id, name, brand, barcode FROM food_log.foods
           WHERE barcode = ANY($1::text[])
           ORDER BY array_position($1::text[], barcode) LIMIT 1""",
        variants,
    )


async def food_detail(conn: asyncpg.Connection, food_id: int) -> dict | None:
    food = await conn.fetchrow(
        "SELECT id, source, source_id, name, brand, barcode FROM food_log.foods WHERE id = $1",
        food_id,
    )
    if food is None:
        return None
    portions = await conn.fetch(
        "SELECT id, description, gram_weight FROM food_log.portions WHERE food_id = $1 ORDER BY id",
        food_id,
    )
    nutrients = await conn.fetch(
        "SELECT nutrient_key, amount_per_100g FROM food_log.nutrients WHERE food_id = $1 ORDER BY nutrient_key",
        food_id,
    )
    return {
        "food": dict(food),
        "portions": [dict(p) for p in portions],
        "nutrients": {n["nutrient_key"]: float(n["amount_per_100g"]) for n in nutrients},
    }
