# food-logger

Self-hosted food logging app replacing MacroFactor's logging and readouts. Personal use.

- **Backend:** Python 3.12 + FastAPI, plain SQL against Neon Postgres (dedicated `food_log` schema)
- **Frontend:** installable PWA (Vite), used in Chrome on iOS — photo-first AI plate estimation, barcode scan, one-tap favorites
- **Food data:** USDA FoodData Central (bulk import + API), Open Food Facts barcode fallback
- **Deploy:** Railway; API secured by static bearer token, `/health` public
- **MCP:** server exposing search/log/summary tools to Claude

**Read [`PLAN.md`](./PLAN.md) before writing any code.** It is the source of truth for scope, phases, schema design, cross-cutting decisions, and working agreements. Each phase ends working and tested per its "done when" criteria.

## Status

Phase 0 built (scaffold, schema, migrate, auth perimeter, barcode spike page). Remaining Phase 0 "done when" items are operator steps: Railway deploy + the on-phone barcode spike test (below).

## Local run

```bash
cp .env.example .env               # fill in DATABASE_URL and API_TOKEN
pip install -r backend/requirements.txt

cd backend
python -m app.migrate              # applies db/food_log_schema.pg.sql + db/migrations/*.sql
python -m uvicorn app.main:app --reload --port 8000

# verify
curl localhost:8000/health                                        # 200, public
curl -H "Authorization: Bearer $API_TOKEN" localhost:8000/api/me  # 200 → user 1
```

Tests: `cd backend && python -m pytest`

Migrations are **never** applied on app startup — only via `python -m app.migrate` (the Neon instance is shared with other schemas; see PLAN working agreements).

## Railway deploy

1. New Railway service pointed at this repo (Nixpacks auto-detects Python via root `requirements.txt`; start command and `/health` healthcheck are in `railway.json`).
2. Set env vars: `DATABASE_URL` (Neon), `API_TOKEN` (generate: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`).
3. Run the migration once against Neon. Easiest is the **Railway service Console** (a shell inside the running container, with `DATABASE_URL` already injected and Neon reachable):
   ```
   cd backend && /opt/venv/bin/python -m app.migrate
   ```
   Use the venv's Python (`/opt/venv/bin/python`), not bare `python` — Nixpacks installs deps into the venv, and the interactive console's default `python` is the base Nix interpreter without `asyncpg`. Expect `applied food_log_schema.pg.sql` (re-runs print `migrations: up to date`). Running it locally against the Neon URL works too.
4. Verify: `https://<app>.up.railway.app/health` answers with `"db":"configured"`; `/api/me` 401s without the token and 200s with it.

## Phase 0 barcode spike (on-phone test)

Open `https://<app>.up.railway.app/spike/` in Chrome on iOS → share → Add to Home Screen → launch from the home screen (standalone mode is the point of the test) → try **Start ZXing** and **Start html5-qrcode** on a real product barcode. The page shows detections with decode timing, plus any camera errors. Record which library won and any iOS quirks here, then the spike page gets deleted in Phase 4.
