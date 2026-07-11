import os
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from .auth import get_current_user

VERSION = "0.1.0"

app = FastAPI(title="food-logger", version=VERSION)


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
