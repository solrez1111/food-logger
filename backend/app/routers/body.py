"""Phase 3 — body weight (lbs in, lbs out) and targets versioned by
effective_date (PLAN). One weight per (user, date); re-posting a date updates
it. Current target = latest effective_date <= the requested day.
"""
from __future__ import annotations

from datetime import date as Date

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..db import get_conn

router = APIRouter(prefix="/api")


class WeightIn(BaseModel):
    date: Date               # client-local (decision 2)
    weight_lb: float = Field(gt=0, lt=1500)


@router.post("/weight", status_code=201)
async def upsert_weight(
    body: WeightIn,
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    row = await conn.fetchrow(
        """INSERT INTO food_log.body_weight (user_id, date, weight_lb)
           VALUES ($1, $2, $3)
           ON CONFLICT (user_id, date)
           DO UPDATE SET weight_lb = EXCLUDED.weight_lb, logged_at = now()
           RETURNING date, weight_lb::float, logged_at""",
        user["id"], body.date, body.weight_lb,
    )
    return dict(row)


@router.get("/weight")
async def list_weights(
    start: Date | None = Query(default=None),
    end: Date | None = Query(default=None),
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    rows = await conn.fetch(
        """SELECT date, weight_lb::float, logged_at FROM food_log.body_weight
           WHERE user_id = $1
             AND ($2::date IS NULL OR date >= $2)
             AND ($3::date IS NULL OR date <= $3)
           ORDER BY date""",
        user["id"], start, end,
    )
    return {"weights": [dict(r) for r in rows]}


class TargetIn(BaseModel):
    effective_date: Date     # client-local; versioning key
    kcal: float | None = Field(default=None, gt=0)
    protein_g: float | None = Field(default=None, ge=0)
    carbs_g: float | None = Field(default=None, ge=0)
    fat_g: float | None = Field(default=None, ge=0)
    fiber_g: float | None = Field(default=None, ge=0)
    sodium_mg: float | None = Field(default=None, ge=0)   # first-class, decision 8


@router.put("/targets", status_code=201)
async def put_target(
    body: TargetIn,
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    if all(getattr(body, k) is None for k in
           ("kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "sodium_mg")):
        raise HTTPException(status_code=422, detail="target must set at least one value")
    row = await conn.fetchrow(
        """INSERT INTO food_log.targets
             (user_id, effective_date, kcal, protein_g, carbs_g, fat_g, fiber_g, sodium_mg)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (user_id, effective_date) DO UPDATE SET
             kcal = EXCLUDED.kcal, protein_g = EXCLUDED.protein_g,
             carbs_g = EXCLUDED.carbs_g, fat_g = EXCLUDED.fat_g,
             fiber_g = EXCLUDED.fiber_g, sodium_mg = EXCLUDED.sodium_mg
           RETURNING effective_date, kcal::float, protein_g::float, carbs_g::float,
                     fat_g::float, fiber_g::float, sodium_mg::float""",
        user["id"], body.effective_date, body.kcal, body.protein_g, body.carbs_g,
        body.fat_g, body.fiber_g, body.sodium_mg,
    )
    return dict(row)


@router.get("/targets")
async def get_targets(
    on: Date | None = Query(default=None, description="client-local date; defaults to server today"),
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    on = on or Date.today()
    current = await conn.fetchrow(
        """SELECT effective_date, kcal::float, protein_g::float, carbs_g::float,
                  fat_g::float, fiber_g::float, sodium_mg::float
           FROM food_log.targets
           WHERE user_id = $1 AND effective_date <= $2
           ORDER BY effective_date DESC LIMIT 1""",
        user["id"], on,
    )
    history = await conn.fetch(
        "SELECT effective_date FROM food_log.targets WHERE user_id = $1 ORDER BY effective_date DESC",
        user["id"],
    )
    return {
        "on": on,
        "current": dict(current) if current else None,
        "versions": [r["effective_date"] for r in history],
    }
