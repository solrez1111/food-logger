import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth import get_current_user
from .db import close_pool, init_pool
from .routers.body import router as body_router
from .routers.estimate import router as estimate_router
from .routers.foods import router as foods_router
from .routers.logs import router as logs_router
from .routers.summary import router as summary_router

VERSION = "0.5.0"


# Remote MCP (Phase 6): mounted at /mcp/{API_TOKEN} for claude.ai custom
# connectors. The unguessable path is the perimeter — same secret as the
# bearer token, so the URL must be treated like a password (OAuth is 6b).
_mcp_token = os.environ.get("API_TOKEN", "")
_mcp = None
if _mcp_token:
    from .mcp_server import build_mcp
    _mcp = build_mcp()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_pool()
    try:
        async with AsyncExitStack() as stack:
            if _mcp is not None:
                try:
                    await stack.enter_async_context(_mcp.session_manager.run())
                except RuntimeError:
                    # A session manager only runs once per process. In prod the
                    # lifespan runs once so this never fires; under pytest each
                    # module re-enters the lifespan — /mcp is inert there, the
                    # API is unaffected.
                    pass
            yield
    finally:
        await close_pool()


app = FastAPI(title="food-logger", version=VERSION, lifespan=lifespan)
if _mcp is not None:
    _mcp_prefix = f"/mcp/{_mcp_token}"
    app.mount(_mcp_prefix, _mcp.streamable_http_app())

    class _McpTrailingSlash:
        """The mounted MCP app answers at '<prefix>/'; users paste the URL
        without the slash. Rewrite so both forms reach it."""
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope.get("type") == "http" and scope.get("path") == _mcp_prefix:
                scope = {**scope, "path": _mcp_prefix + "/"}
            await self.inner(scope, receive, send)

    app.add_middleware(_McpTrailingSlash)
app.include_router(foods_router)
app.include_router(estimate_router)   # /api/log/estimate — before logs' /api/log/{...} routes
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
