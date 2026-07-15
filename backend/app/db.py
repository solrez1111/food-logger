"""App-wide asyncpg pool. Created in the FastAPI lifespan; routes acquire
connections via the get_conn dependency. A missing DATABASE_URL leaves the
pool unset and data routes answer 503 — /health stays up either way.
"""
from __future__ import annotations

import os

import asyncpg
from fastapi import HTTPException

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    url = os.environ.get("DATABASE_URL")
    if url:
        _pool = await asyncpg.create_pool(url, min_size=1, max_size=5)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def get_conn():
    if _pool is None:
        raise HTTPException(status_code=503, detail="database not configured")
    async with _pool.acquire() as conn:
        yield conn
