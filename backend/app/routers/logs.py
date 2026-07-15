"""Phase 3 — logging API: log entries CRUD + favorites (PLAN).

Key behaviors: dates are CLIENT-LOCAL and arrive from the client (decision 2);
grams are canonical (portion input is converted server-side); replays of the
same client_id are idempotent (outbox retries, decision 5); no meal concept
(decision 7).
"""
from __future__ import annotations

import uuid
from datetime import date as Date
from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from .. import food_db
from ..auth import get_current_user
from ..db import get_conn

router = APIRouter(prefix="/api", dependencies=[])

ENTRY_METHODS = ("manual", "barcode", "favorite", "ai_photo", "ai_text", "mcp")


async def _portion_grams(conn: asyncpg.Connection, food_id: int, portion_id: int, qty: float) -> float:
    row = await conn.fetchrow(
        "SELECT gram_weight::float AS gw FROM food_log.portions WHERE id = $1 AND food_id = $2",
        portion_id, food_id,
    )
    if row is None:
        raise HTTPException(status_code=422, detail="portion does not belong to that food")
    return round(row["gw"] * qty, 2)


async def _entry_out(conn: asyncpg.Connection, entry: asyncpg.Record) -> dict:
    food = await conn.fetchrow(
        "SELECT id, name, brand FROM food_log.foods WHERE id = $1", entry["food_id"]
    )
    macros = await food_db.macro_previews(conn, [entry["food_id"]])
    grams = float(entry["grams"])
    per_entry = {k: round(v * grams / 100, 1) for k, v in macros.get(entry["food_id"], {}).items()}
    portion = None
    if entry["portion_id"] is not None:
        p = await conn.fetchrow(
            "SELECT id, description, gram_weight::float AS gram_weight FROM food_log.portions WHERE id = $1",
            entry["portion_id"],
        )
        if p is not None:
            portion = {**dict(p), "qty": float(entry["portion_qty"] or 0) or None}
    return {
        "id": entry["id"], "date": entry["date"], "grams": grams,
        "food": dict(food) if food else None, "portion": portion,
        "logged_at": entry["logged_at"], "entry_method": entry["entry_method"],
        "client_id": str(entry["client_id"]), "per_entry": per_entry,
    }


class LogIn(BaseModel):
    date: Date
    client_id: uuid.UUID | None = None      # supply for idempotent outbox retries
    favorite_id: int | None = None          # shorthand: food+amount from a favorite
    food_id: int | None = None
    grams: float | None = Field(default=None, gt=0)
    portion_id: int | None = None
    portion_qty: float | None = Field(default=None, gt=0)
    entry_method: Literal["manual", "barcode", "favorite", "ai_photo", "ai_text", "mcp"] = "manual"

    @model_validator(mode="after")
    def check_amount_shape(self):
        if self.favorite_id is not None:
            return self
        if self.food_id is None:
            raise ValueError("food_id required (or favorite_id)")
        has_grams = self.grams is not None
        has_portion = self.portion_id is not None and self.portion_qty is not None
        if has_grams == has_portion:
            raise ValueError("provide grams OR portion_id+portion_qty")
        return self


@router.post("/log", status_code=201)
async def create_log(
    body: LogIn,
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    food_id, grams = body.food_id, body.grams
    portion_id, portion_qty = body.portion_id, body.portion_qty
    entry_method = body.entry_method

    if body.favorite_id is not None:
        fav = await conn.fetchrow(
            """SELECT f.food_id, f.default_grams::float AS default_grams,
                      f.portion_id, f.portion_qty::float AS portion_qty
               FROM food_log.favorites f WHERE f.id = $1 AND f.user_id = $2""",
            body.favorite_id, user["id"],
        )
        if fav is None:
            raise HTTPException(status_code=404, detail="favorite not found")
        food_id, portion_id, portion_qty = fav["food_id"], fav["portion_id"], fav["portion_qty"]
        grams = fav["default_grams"]
        entry_method = "favorite"

    if not await conn.fetchval("SELECT 1 FROM food_log.foods WHERE id = $1", food_id):
        raise HTTPException(status_code=404, detail="food not found")
    if grams is None:
        grams = await _portion_grams(conn, food_id, portion_id, portion_qty)

    client_id = body.client_id or uuid.uuid4()
    entry = await conn.fetchrow(
        """INSERT INTO food_log.log_entries
             (user_id, date, food_id, grams, portion_id, portion_qty, client_id, entry_method)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (client_id) DO NOTHING
           RETURNING *""",
        user["id"], body.date, food_id, grams, portion_id, portion_qty, client_id, entry_method,
    )
    if entry is not None:
        return await _entry_out(conn, entry)

    # replay: same client_id already logged — return the existing entry, no dup
    existing = await conn.fetchrow(
        "SELECT * FROM food_log.log_entries WHERE client_id = $1 AND user_id = $2",
        client_id, user["id"],
    )
    if existing is None:  # someone else's client_id — treat as conflict
        raise HTTPException(status_code=409, detail="client_id already used")
    return await _entry_out(conn, existing)


@router.get("/log/{day}")
async def get_log_day(
    day: Date,
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    rows = await conn.fetch(
        "SELECT * FROM food_log.log_entries WHERE user_id = $1 AND date = $2 ORDER BY logged_at, id",
        user["id"], day,
    )
    return {"date": day, "entries": [await _entry_out(conn, r) for r in rows]}


class LogPatch(BaseModel):
    date: Date | None = None
    grams: float | None = Field(default=None, gt=0)
    portion_id: int | None = None
    portion_qty: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def check_something_changes(self):
        if all(v is None for v in (self.date, self.grams, self.portion_id, self.portion_qty)):
            raise ValueError("nothing to change")
        if (self.portion_id is None) != (self.portion_qty is None):
            raise ValueError("portion_id and portion_qty go together")
        if self.grams is not None and self.portion_id is not None:
            raise ValueError("provide grams OR portion, not both")
        return self


@router.patch("/log/{entry_id}")
async def patch_log(
    entry_id: int,
    body: LogPatch,
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    entry = await conn.fetchrow(
        "SELECT * FROM food_log.log_entries WHERE id = $1 AND user_id = $2", entry_id, user["id"]
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="entry not found")

    date = body.date or entry["date"]
    grams, portion_id, portion_qty = entry["grams"], entry["portion_id"], entry["portion_qty"]
    if body.grams is not None:
        grams, portion_id, portion_qty = body.grams, None, None
    elif body.portion_id is not None:
        portion_id, portion_qty = body.portion_id, body.portion_qty
        grams = await _portion_grams(conn, entry["food_id"], portion_id, portion_qty)

    updated = await conn.fetchrow(
        """UPDATE food_log.log_entries
           SET date = $3, grams = $4, portion_id = $5, portion_qty = $6
           WHERE id = $1 AND user_id = $2 RETURNING *""",
        entry_id, user["id"], date, grams, portion_id, portion_qty,
    )
    return await _entry_out(conn, updated)


@router.delete("/log/{entry_id}", status_code=204)
async def delete_log(
    entry_id: int,
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    deleted = await conn.fetchval(
        "DELETE FROM food_log.log_entries WHERE id = $1 AND user_id = $2 RETURNING id",
        entry_id, user["id"],
    )
    if deleted is None:
        raise HTTPException(status_code=404, detail="entry not found")


# --- favorites ---------------------------------------------------------------

class FavoriteIn(BaseModel):
    food_id: int
    default_grams: float | None = Field(default=None, gt=0)
    portion_id: int | None = None
    portion_qty: float | None = Field(default=None, gt=0)
    label: str | None = Field(default=None, max_length=100)
    position: int = 0

    @model_validator(mode="after")
    def check_amount_shape(self):
        has_grams = self.default_grams is not None
        has_portion = self.portion_id is not None and self.portion_qty is not None
        if has_grams == has_portion:
            raise ValueError("provide default_grams OR portion_id+portion_qty")
        return self


@router.get("/favorites")
async def list_favorites(
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    rows = await conn.fetch(
        """SELECT fav.id, fav.food_id, fav.default_grams::float AS default_grams,
                  fav.portion_id, fav.portion_qty::float AS portion_qty,
                  fav.label, fav.position,
                  f.name, f.brand, p.description AS portion_description,
                  p.gram_weight::float AS portion_gram_weight
           FROM food_log.favorites fav
           JOIN food_log.foods f ON f.id = fav.food_id
           LEFT JOIN food_log.portions p ON p.id = fav.portion_id
           WHERE fav.user_id = $1
           ORDER BY fav.position, fav.id""",
        user["id"],
    )
    macros = await food_db.macro_previews(conn, [r["food_id"] for r in rows])
    out = []
    for r in rows:
        grams = r["default_grams"] or round(r["portion_gram_weight"] * r["portion_qty"], 2)
        per_serving = {k: round(v * grams / 100, 1) for k, v in macros.get(r["food_id"], {}).items()}
        out.append({
            "id": r["id"], "food_id": r["food_id"],
            "name": r["label"] or r["name"], "food_name": r["name"], "brand": r["brand"],
            "grams": grams, "portion_description": r["portion_description"],
            "position": r["position"], "per_serving": per_serving,
        })
    return {"favorites": out}


@router.post("/favorites", status_code=201)
async def create_favorite(
    body: FavoriteIn,
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    if not await conn.fetchval("SELECT 1 FROM food_log.foods WHERE id = $1", body.food_id):
        raise HTTPException(status_code=404, detail="food not found")
    if body.portion_id is not None:
        await _portion_grams(conn, body.food_id, body.portion_id, body.portion_qty)  # validates ownership
    row = await conn.fetchrow(
        """INSERT INTO food_log.favorites
             (user_id, food_id, default_grams, portion_id, portion_qty, label, position)
           VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
        user["id"], body.food_id, body.default_grams, body.portion_id, body.portion_qty,
        body.label, body.position,
    )
    return {"id": row["id"]}


@router.delete("/favorites/{favorite_id}", status_code=204)
async def delete_favorite(
    favorite_id: int,
    user: dict = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    deleted = await conn.fetchval(
        "DELETE FROM food_log.favorites WHERE id = $1 AND user_id = $2 RETURNING id",
        favorite_id, user["id"],
    )
    if deleted is None:
        raise HTTPException(status_code=404, detail="favorite not found")
