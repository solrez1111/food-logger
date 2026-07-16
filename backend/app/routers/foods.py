"""Phase 2 — search & matching API (PLAN).

Search is LOCAL-ONLY by default (decision 3): FTS ranked by ts_rank, trigram
word_similarity fallback for typos (>0.4 cutoff — the <% operator's 0.6
default misses 'yogrt' vs 'Greek Yogurt Plain', verified in Phase 0). Live FDC
search never fires implicitly; the response's offer_remote flag tells the UI
when to show "Search USDA →", which re-calls with remote=1.
"""
from __future__ import annotations

import re
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

router = APIRouter(prefix="/api/foods", dependencies=[Depends(get_current_user)])

# Search strategy (tuned against a realistic USDA-name corpus — see
# tests/test_search_quality.py):
#   1. Match ANY query word (OR, with prefix on every term so search-as-you-
#      type works: 'scram' finds scrambled). Pure AND returned nothing for
#      "overeasy egg" — one out-of-vocabulary word killed the whole query.
#   2. Rank rows matching ALL words first, then by word_similarity(query,
#      name) — plain ts_rank ties on almost every row, and the old
#      shortest-name tiebreak put "Bread, egg (challah)" above actual eggs.
#   3. Trigram fallback only when no word matches at all (typos: 'yogrt').
SEARCH_FTS = """
SELECT f.id, f.source, f.name, f.brand, f.barcode,
       (f.search_vec @@ to_tsquery('english', $2))::int AS full_match,
       -- USDA names lead with the food itself ("Egg, whole, ..."), so a name
       -- whose FIRST word is one of the query words is almost always the
       -- thing being searched for, not a dish containing it ("Bread, egg").
       ((lower(substring(f.name from '^[A-Za-z0-9]+')) = ANY($5))::int * 3
        + word_similarity($1, f.name) * 2
        + ts_rank(f.search_vec, to_tsquery('english', $3)))::float AS score
FROM food_log.foods f
WHERE f.search_vec @@ to_tsquery('english', $3)
ORDER BY full_match DESC, score DESC, length(f.name) ASC
LIMIT $4
"""

SEARCH_TRGM = """
SELECT f.id, f.source, f.name, f.brand, f.barcode,
       1 AS full_match,
       word_similarity($1, f.name)::float AS score
FROM food_log.foods f
WHERE word_similarity($1, f.name) > 0.4
ORDER BY score DESC, length(f.name) ASC
LIMIT $2
"""

_WORD_RE = re.compile(r"[a-z0-9]+")


def _ts_queries(q: str) -> tuple[str, str, list[str]] | None:
    """(AND-query, OR-query, raw terms) with prefix matching on every term, or
    None if the input has no usable words. Tokens are [a-z0-9]+ only — safe to
    splice into to_tsquery."""
    terms = _WORD_RE.findall(q.lower())[:8]
    if not terms:
        return None
    return (
        " & ".join(f"{t}:*" for t in terms),
        " | ".join(f"{t}:*" for t in terms),
        terms,
    )


async def _local_search(conn: asyncpg.Connection, q: str, limit: int):
    queries = _ts_queries(q)
    rows = []
    if queries:
        and_q, or_q, terms = queries
        rows = await conn.fetch(SEARCH_FTS, q, and_q, or_q, limit, terms)
    if rows:
        matched = "fts" if rows[0]["full_match"] else "fts_partial"
        return rows, matched
    rows = await conn.fetch(SEARCH_TRGM, q, limit)
    return rows, ("trgm" if rows else "none")


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

    rows, matched = await _local_search(conn, q, limit)
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
