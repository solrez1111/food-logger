"""Phase 2 — search & matching API (PLAN).

Search is LOCAL-ONLY by default (decision 3): FTS ranked by ts_rank, trigram
word_similarity fallback for typos (>0.4 cutoff — the <% operator's 0.6
default misses 'yogrt' vs 'Greek Yogurt Plain', verified in Phase 0). Live FDC
search never fires implicitly; the response's offer_remote flag tells the UI
when to show "Search USDA →", which re-calls with remote=1.
"""
from __future__ import annotations

import uuid

import asyncpg
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import food_db
from ..auth import get_current_user
from ..db import get_conn
from ..food_sources import fdc_search, resolve_barcode
from ..normalize import fdc_api_food
from ..search import local_search

router = APIRouter(prefix="/api/foods", dependencies=[Depends(get_current_user)])

def _result(row: asyncpg.Record, macros: dict) -> dict:
    return {
        "id": row["id"], "source": row["source"], "name": row["name"],
        "brand": row["brand"], "barcode": row["barcode"],
        "score": round(row["score"], 4), "per_100g": macros.get(row["id"], {}),
    }


@router.get("/search")
async def search_foods(
    q: str = Query(min_length=2, max_length=200),
    remote: bool = False,
    limit: int = Query(20, ge=1, le=50),
    conn: asyncpg.Connection = Depends(get_conn),
):
    if remote:
        # Explicit "Search USDA →": import supported hits, then serve locally.
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                hits = await fdc_search(client, q, page_size=limit)
        except RuntimeError as e:            # FDC_API_KEY not set
            raise HTTPException(status_code=503, detail=str(e))
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"FDC search failed: {e}")
        for payload in hits:
            item = fdc_api_food(payload)
            if item:
                item["raw"] = payload
                await food_db.upsert_food(conn, item)

    rows, matched = await local_search(conn, q, limit)
    macros = await food_db.macro_previews(conn, [r["id"] for r in rows])
    results = [_result(r, macros) for r in rows]
    return {
        "query": q,
        "matched": "remote" if remote else matched,
        "results": results,
        # UI shows "Search USDA →" when local results look weak (decision 3).
        "offer_remote": not remote and (matched != "fts" or len(results) < 3),
    }


@router.get("/barcode/{code}")
async def barcode_lookup(code: str, conn: asyncpg.Connection = Depends(get_conn)):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            detail = await resolve_barcode(conn, client, code)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"upstream barcode lookup failed: {e}")
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no food found for barcode {code}")
    return detail


class PortionIn(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    gram_weight: float = Field(gt=0)


class CustomFoodIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    brand: str | None = Field(default=None, max_length=200)
    barcode: str | None = Field(default=None, max_length=32)
    portions: list[PortionIn] = []
    nutrients: dict[str, float] = Field(min_length=1)  # per-100g, snake keys e.g. kcal, protein_g


@router.post("", status_code=201)
async def create_custom_food(body: CustomFoodIn, conn: asyncpg.Connection = Depends(get_conn)):
    for key, val in body.nutrients.items():
        if not key.replace("_", "").isalnum() or key != key.lower():
            raise HTTPException(status_code=422, detail=f"bad nutrient key: {key}")
        if val < 0:
            raise HTTPException(status_code=422, detail=f"negative amount for {key}")
    item = {
        "food": {
            "source": "custom", "source_id": uuid.uuid4().hex,
            "name": body.name.strip(), "brand": (body.brand or "").strip() or None,
            "barcode": (body.barcode or "").strip() or None,
        },
        "portions": [p.model_dump() for p in body.portions],
        "nutrients": body.nutrients,
    }
    food_id = await food_db.upsert_food(conn, item, keep_raw=False)
    return await food_db.food_detail(conn, food_id)


@router.get("/{food_id}")
async def get_food(food_id: int, conn: asyncpg.Connection = Depends(get_conn)):
    detail = await food_db.food_detail(conn, food_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="food not found")
    return detail
