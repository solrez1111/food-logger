# food-logger

Self-hosted food logging app replacing MacroFactor's logging and readouts. Personal use.

- **Backend:** Python 3.12 + FastAPI, plain SQL against Neon Postgres (dedicated `food_log` schema)
- **Frontend:** installable PWA (Vite), used in Chrome on iOS — photo-first AI plate estimation, barcode scan, one-tap favorites
- **Food data:** USDA FoodData Central (bulk import + API), Open Food Facts barcode fallback
- **Deploy:** Railway; API secured by static bearer token, `/health` public
- **MCP:** server exposing search/log/summary tools to Claude

**Read [`PLAN.md`](./PLAN.md) before writing any code.** It is the source of truth for scope, phases, schema design, cross-cutting decisions, and working agreements. Each phase ends working and tested per its "done when" criteria.

## Status

- **Phase 0 complete** — deployed on Railway, schema migrated into Neon, barcode spike passed on-phone (**ZXing wins**; html5-qrcode dropped — see PLAN spike outcome).
- **Phase 1 complete** — 8,204 foods (FDC Foundation + SR Legacy) loaded into Neon with per-100g nutrition, portions, and search indexes.
- **Phase 2 built** — search & matching API: `GET /api/foods/search` (FTS + trigram typo fallback, `remote=1` for explicit USDA import), `GET /api/foods/barcode/{code}` (local → OFF → FDC chain), `GET /api/foods/{id}`, `POST /api/foods` (custom foods).
- **Phase 3 built** — logging API: `POST/GET/PATCH/DELETE /api/log` (client-local dates, grams-or-portion input, client_id idempotency), `GET /api/summary/{date}` and `?start=&end=` (macro+sodium rollups with coverage, target remaining), `GET /api/summary/nutrient/{key}` (any stored nutrient), `POST/GET /api/weight`, `PUT/GET /api/targets` (effective-date versioned), `POST/GET/DELETE /api/favorites` (one-tap logging via `favorite_id`).

**Outstanding operator steps:** run migration 0002 in the Railway console (`cd backend && /opt/venv/bin/python -m app.migrate`), then the Phase 2 prod smoke (search + one barcode lookup).

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

**Result (July 2026, iOS 18.7):** camera + continuous decode works from a home-screen standalone PWA. ZXing read UPC-A/EAN-13/EAN-8 accurately; html5-qrcode got no 1D reads and is dropped. Friction is aiming, not decode — Phase 4 ships a reticle, tap-to-focus, and torch toggle. The `/spike/` page gets deleted in Phase 4.

## Phase 1: loading the food catalog

**Bulk import (~15k generic foods, no API key).** From the Railway service Console:

```bash
cd backend
curl -LO https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_foundation_food_csv_2025-04-24.zip
curl -LO https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip
/opt/venv/bin/python import/fdc_bulk.py --zip FoodData_Central_foundation_food_csv_2025-04-24.zip --zip FoodData_Central_sr_legacy_food_csv_2018-04.zip
```

(If a URL 404s, grab the current CSV links from https://fdc.nal.usda.gov/download-datasets — "Foundation Foods" and "SR Legacy".) Re-running is a safe idempotent refresh.

**On-demand imports** (need `FDC_API_KEY` set):

```bash
/opt/venv/bin/python import/fdc_import.py --search "greek yogurt"          # list FDC hits
/opt/venv/bin/python import/fdc_import.py --fdc-id 173410                  # import one
/opt/venv/bin/python import/off_lookup.py --barcode 070734000034           # local → OFF → FDC branded, caches hit
```

Nutrition is stored per-100g; every nutrient the source reports is kept (snake_case keys). Keys are canonical **across sources** (an FDC food and an OFF food both store `potassium_mg` — see `FDC_CANONICAL` in `app/normalize.py`; migration 0002 renamed pre-existing rows). Macro keys the app's rollups depend on: `kcal`, `protein_g`, `carbs_g`, `fat_g`, `fiber_g`, `sodium_mg`. Also canonical: potassium, magnesium, calcium and the other DASH-relevant minerals, cholesterol, saturated/trans fat, sugars, caffeine, and `alcohol_g` (dashboard cutover compatibility).
