"""Phase 3 — daily rollups with coverage (PLAN decisions 4 and 8).

Coverage for nutrient k on a day = grams logged from foods that report k
÷ total grams logged that day. It makes "low sodium" distinguishable from
"sodium unlogged" — surfaced with every nutrient total, never dropped.
"""
from __future__ import annotations

import re
from datetime import date as Date

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_current_user
from ..db import get_conn

router = APIRouter(prefix="/api/summary")

# Macro tier for the day view; sodium is deliberately target-tier (decision 8).
MACRO_KEYS = ("kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "sodium_mg")
KEY_RE = re.compile(r"^[a-z0-9_]{1,64}$")

DAY_TOTALS = """
SELECT date, SUM(grams)::float AS total_grams, COUNT(*)::int AS n_entries
FROM food_log.log_entries
WHERE user_id = $1 AND date BETWEEN $2 AND $3
GROUP BY date
"""

NUTRIENT_ROLLUP = """
SELECT e.date, n.nutrient_key,
       SUM(e.grams * n.amount_per_100g / 100)::float AS total,
       SUM(e.grams)::float AS covered_grams
FROM food_log.log_entries e
JOIN food_log.nutrients n ON n.food_id = e.food_id AND n.nutrient_key = ANY($4)
WHERE e.user_id = $1 AND e.date BETWEEN $2 AND $3
GROUP BY e.date, n.nutrient_key
"""


async def _rollup(conn: asyncpg.Connection, user_id: int, start: Date, end: Date,
                  keys: tuple[str, ...]) -> dict[Date, dict]:
    """{date: {total_grams, n_entries, nutrients: {key: {total, coverage}}}}"""
    days: dict[Date, dict] = {}
    for r in await conn.fetch(DAY_TOTALS, user_id, start, end):
        days[r["date"]] = {
            "total_grams": round(r["total_grams"], 1),
            "n_entries": r["n_entries"],
            "nutrients": {},
        }
    for r in await conn.fetch(NUTRIENT_ROLLUP, user_id, start, end, list(keys)):
        day = days[r["date"]]
        coverage = min(1.0, r["covered_grams"] / day["total_grams"]) if day["total_grams"] else 0.0
        day["nutrients"][r["nutrient_key"]] = {
            "total": round(r["total"], 1),
            "coverage": round(coverage, 3),
        }
    # zero-coverage keys still appear — an absent number reads as a fake zero
    for day in days.values():
        for k in keys:
            day["nutrients"].setdefault(k, {"total": None, "coverage": 0.0})
    return days


async def _current_target(conn: asyncpg.Connection, user_id: int, on: Date) -> dict | None:
    row = await conn.fetchrow(
        """SELECT effective_date, kcal::float, protein_g::float, carbs_g::float,
                  fat_g::float, fiber_g::float, sodium_mg::float
           FROM food_log.targets
           WHERE user_id = $1 AND effective_date <= $2
           ORDER BY effective_date DESC LIMIT 1""",
        user_id, on,
    )
    return dict(row) if row else None


# NOTE: declared before /{day} so the literal path wins.
@router.get("/nutrient/{key}")
async def nutrient_summary(
    key: str,
    start: Date = Query(...),
    end: Date = Query(...),
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    if not KEY_RE.match(key):
        raise HTTPException(status_code=422, detail="bad nutrient key")
    if end < start:
        raise HTTPException(status_code=422, detail="end before start")
    days = await _rollup(conn, user["id"], start, end, (key,))
    return {
        "nutrient": key, "start": start, "end": end,
        "days": [
            {"date": d, "total": v["nutrients"][key]["total"],
             "coverage": v["nutrients"][key]["coverage"], "n_entries": v["n_entries"]}
            for d, v in sorted(days.items())
        ],
    }


@router.get("/{day}")
async def day_summary(
    day: Date,
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    days = await _rollup(conn, user["id"], day, day, MACRO_KEYS)
    data = days.get(day, {"total_grams": 0.0, "n_entries": 0,
                          "nutrients": {k: {"total": None, "coverage": 0.0} for k in MACRO_KEYS}})
    target = await _current_target(conn, user["id"], day)
    remaining = None
    if target:
        remaining = {}
        for k in MACRO_KEYS:
            if target.get(k) is not None:
                logged = data["nutrients"][k]["total"] or 0.0
                remaining[k] = round(target[k] - logged, 1)
    return {"date": day, **data, "target": target, "remaining": remaining}


@router.get("")
async def range_summary(
    start: Date = Query(...),
    end: Date = Query(...),
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    if end < start:
        raise HTTPException(status_code=422, detail="end before start")
    if (end - start).days > 400:
        raise HTTPException(status_code=422, detail="range too large (max 400 days)")
    days = await _rollup(conn, user["id"], start, end, MACRO_KEYS)
    return {
        "start": start, "end": end,
        "days": [{"date": d, **v} for d, v in sorted(days.items())],
    }
