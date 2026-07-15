import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth import get_current_user
from .db import close_pool, init_pool
from .routers.body import router as body_router
from .routers.foods import router as foods_router
from .routers.logs import router as logs_router
from .routers.summary import router as summary_router

VERSION = "0.3.0"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(title="food-logger", version=VERSION, lifespan=lifespan)
app.include_router(foods_router)
app.include_router(logs_router)
app.include_router(summary_router)
app.include_router(body_router)


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


# Serve the built PWA (frontend/dist). API routes are registered above and
# win; anything else falls back to index.html so the app owns its URL space.
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        candidate = _dist / path
        if path and candidate.is_file() and candidate.resolve().is_relative_to(_dist):
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")
