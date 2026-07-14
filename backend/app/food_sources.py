"""Upstream food-data fetchers + the barcode resolution chain.

Chain (PLAN Phase 1): local DB -> Open Food Facts -> FDC branded search.
Everything fetched is cached locally via upsert_food, so second lookups
never leave the database.
"""
from __future__ import annotations

import os

import asyncpg
import httpx

from . import food_db
from .normalize import barcode_variants, fdc_api_food, off_food

FDC_BASE = "https://api.nal.usda.gov/fdc/v1"
OFF_BASE = "https://world.openfoodfacts.org/api/v2"
# OFF asks API users to identify themselves.
OFF_HEADERS = {"User-Agent": "food-logger/0.1 (personal use)"}


def _fdc_key() -> str:
    key = os.environ.get("FDC_API_KEY", "")
    if not key:
        raise RuntimeError("FDC_API_KEY not set")
    return key


async def fdc_get_food(client: httpx.AsyncClient, fdc_id: str | int) -> dict:
    res = await client.get(f"{FDC_BASE}/food/{fdc_id}", params={"api_key": _fdc_key()})
    res.raise_for_status()
    return res.json()


async def fdc_search(client: httpx.AsyncClient, query: str, data_types: list[str] | None = None,
                     page_size: int = 10) -> list[dict]:
    params: dict = {"api_key": _fdc_key(), "query": query, "pageSize": page_size}
    if data_types:
        params["dataType"] = ",".join(data_types)
    res = await client.get(f"{FDC_BASE}/foods/search", params=params)
    res.raise_for_status()
    return (res.json() or {}).get("foods") or []


async def off_get_product(client: httpx.AsyncClient, barcode: str) -> dict | None:
    res = await client.get(f"{OFF_BASE}/product/{barcode}.json", headers=OFF_HEADERS)
    if res.status_code == 404:
        return None
    res.raise_for_status()
    body = res.json()
    if body.get("status") != 1:
        return None
    return body.get("product")


async def resolve_barcode(conn: asyncpg.Connection, client: httpx.AsyncClient, code: str) -> dict | None:
    """local DB -> OFF -> FDC branded search; caches upstream hits locally.

    Returns food_db.food_detail() shape, or None when nothing matches.
    """
    hit = await food_db.find_by_barcode(conn, code)
    if hit:
        return await food_db.food_detail(conn, hit["id"])

    for variant in barcode_variants(code):
        product = await off_get_product(client, variant)
        if product:
            item = off_food(variant, product)
            if item:
                item["raw"] = product
                food_id = await food_db.upsert_food(conn, item)
                return await food_db.food_detail(conn, food_id)

    # FDC branded search matches gtinUpc via the plain query string.
    for variant in barcode_variants(code):
        hits = await fdc_search(client, variant, data_types=["Branded"], page_size=3)
        for payload in hits:
            if (payload.get("gtinUpc") or "").lstrip("0") == variant.lstrip("0"):
                item = fdc_api_food(payload)
                if item:
                    item["raw"] = payload
                    food_id = await food_db.upsert_food(conn, item)
                    return await food_db.food_detail(conn, food_id)

    return None
