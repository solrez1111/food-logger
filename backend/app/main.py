import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from .auth import get_current_user
from .db import close_pool, init_pool
from .routers.foods import router as foods_router

VERSION = "0.2.0"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(title="food-logger", version=VERSION, lifespan=lifespan)
app.include_router(foods_router)


# Public health endpoint — used by the Railway healthcheck. Always 200; reports
# config state instead of crashing so a misconfigured deploy is diagnosable.
@app.get("/health")
async def health():
    return {
        "ok": True,
        "version": VERSION,
        "db": "configured" if os.environ.get("DATABASE_URL") else "missing DATABASE_URL",
    }


# Minimal authed endpoint: proves the bearer perimeter end to end (Phase 0
# "done when"), and the PWA's first-run screen will use it to validate a token.
@app.get("/api/me")
async def me(user: dict = Depends(get_current_user)):
    return {"user": user}


# Phase 0 barcode spike — throwaway page, removed in Phase 4. Public by design:
# it makes no API calls; it only exercises camera + decode on iOS WebKit.
_spike_dir = Path(__file__).parent / "static" / "spike"
app.mount("/spike", StaticFiles(directory=_spike_dir, html=True), name="spike")
