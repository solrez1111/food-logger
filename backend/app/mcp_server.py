"""Phase 6 — MCP server exposing the food log to Claude.

Tools are thin wrappers over the HTTP API (never the DB directly) so auth,
validation, idempotency, and rollup logic stay in one place. Two transports:

- stdio: mcp/server.py launcher (Claude Code / Claude Desktop)
- streamable HTTP mounted at /mcp/{API_TOKEN} inside the FastAPI app for
  claude.ai custom connectors. The secret path IS the perimeter — same secret
  as the bearer token, so treat the URL like a password. Proper OAuth is
  Phase 6b.

Env (stdio mode): FOOD_LOG_API_URL, FOOD_LOG_API_TOKEN, FOOD_LOG_TZ.
Mounted mode needs nothing extra — it talks to its own process.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from mcp.server.fastmcp import FastMCP


def _base_url() -> str:
    return (os.environ.get("FOOD_LOG_API_URL")
            or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}").rstrip("/")


def _headers() -> dict:
    token = os.environ.get("FOOD_LOG_API_TOKEN") or os.environ.get("API_TOKEN", "")
    return {"Authorization": f"Bearer {token}"}


def _today() -> str:
    """Client-local date (PLAN decision 2): the MCP host's configured timezone
    stands in for the phone's calendar day."""
    tz = os.environ.get("FOOD_LOG_TZ") or os.environ.get("TZ") or "America/New_York"
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = ZoneInfo("America/New_York")
    return datetime.now(zone).date().isoformat()


async def _call(method: str, path: str, **kwargs) -> dict:
    """Call the API; errors come back as {'error': ...} — friendlier for the
    model than a raised exception, and keeps partial workflows recoverable."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.request(method, _base_url() + path, headers=_headers(), **kwargs)
    except httpx.HTTPError as e:
        return {"error": f"food-log API unreachable: {e}"}
    if res.status_code == 401:
        return {"error": "food-log API rejected the token (check FOOD_LOG_API_TOKEN)"}
    if res.status_code >= 400:
        try:
            detail = res.json().get("detail")
        except Exception:
            detail = res.text[:200]
        return {"error": f"{res.status_code}: {detail}"}
    if res.status_code == 204:
        return {"ok": True}
    return res.json()


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        "food-log",
        instructions=(
            "Personal food log (hypertension-aware: sodium is tracked at the same "
            "tier as calories/protein). Nutrition data always comes from the food "
            "catalog, never invented. Dates are the user's local calendar day."
        ),
        stateless_http=True,
        streamable_http_path="/",
    )

    @mcp.tool()
    async def search_foods(query: str, remote: bool = False, limit: int = 10) -> dict:
        """Search the food catalog. Local-first; set remote=true only if local
        results are inadequate (imports from USDA on the fly). Results include
        per-100g kcal/protein/carbs/fat/sodium previews and food ids for
        log_food."""
        return await _call("GET", f"/api/foods/search?q={httpx.QueryParams({'q': query})['q']}"
                                  f"&remote={'1' if remote else '0'}&limit={limit}")

    @mcp.tool()
    async def log_food(food_id: int, grams: float | None = None,
                       portion_id: int | None = None, portion_qty: float | None = None,
                       date: str | None = None) -> dict:
        """Log one food to the user's day. Provide grams OR portion_id+portion_qty
        (portions come from search/estimate results). date defaults to the
        user's local today (YYYY-MM-DD to override). Returns the saved entry
        with its computed macros."""
        body = {
            "client_id": str(uuid.uuid4()),
            "date": date or _today(),
            "food_id": food_id,
            "entry_method": "mcp",
        }
        if grams is not None:
            body["grams"] = grams
        if portion_id is not None:
            body["portion_id"] = portion_id
            body["portion_qty"] = portion_qty or 1
        return await _call("POST", "/api/log", json=body)

    @mcp.tool()
    async def estimate_plate(description: str) -> dict:
        """Turn a meal description into candidate foods with gram estimates,
        matched against the catalog. IMPORTANT: this does NOT log anything —
        show the candidates (food, grams, kcal) to the user, let them adjust,
        and only then call log_food for each confirmed item (confirm-before-
        save is a hard rule of this app)."""
        return await _call("POST", "/api/log/estimate", json={"text": description})

    @mcp.tool()
    async def get_day_summary(date: str | None = None) -> dict:
        """Daily totals (kcal, protein, carbs, fat, fiber, sodium) with
        coverage figures, the active target, and remaining amounts. Coverage
        < 1.0 means some logged grams don't report that nutrient — say so
        rather than presenting the total as complete. date defaults to the
        user's local today."""
        return await _call("GET", f"/api/summary/{date or _today()}")

    @mcp.tool()
    async def get_trends(days: int = 7) -> dict:
        """Day-by-day macro rollups for the last N days plus body weight
        (lbs) for the last 90. Days with nothing logged are absent, not
        zero — treat missing days as unlogged, never as fasting."""
        today = _today()
        start = (datetime.fromisoformat(today) - timedelta(days=days - 1)).date().isoformat()
        summary = await _call("GET", f"/api/summary?start={start}&end={today}")
        w_start = (datetime.fromisoformat(today) - timedelta(days=89)).date().isoformat()
        weights = await _call("GET", f"/api/weight?start={w_start}&end={today}")
        return {"summary": summary, "weights": weights}

    @mcp.tool()
    async def get_nutrient_summary(nutrient_key: str, days: int = 30) -> dict:
        """Daily totals for ANY stored nutrient key over the last N days, with
        per-day coverage. Keys are snake_case: sodium_mg, potassium_mg,
        magnesium_mg, calcium_mg, fiber_g, caffeine_mg, alcohol_g,
        cholesterol_mg, sugars_total_g, fatty_acids_total_saturated_g, etc."""
        today = _today()
        start = (datetime.fromisoformat(today) - timedelta(days=days - 1)).date().isoformat()
        return await _call("GET", f"/api/summary/nutrient/{nutrient_key}?start={start}&end={today}")

    @mcp.tool()
    async def log_weight(weight_lb: float, date: str | None = None) -> dict:
        """Log body weight in POUNDS for a day (re-logging the same day
        overwrites). date defaults to the user's local today."""
        return await _call("POST", "/api/weight",
                           json={"date": date or _today(), "weight_lb": weight_lb})

    return mcp
