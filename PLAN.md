# Food Logger — Implementation Plan (v3: Railway + Neon)

A self-hosted food logging app to replace MacroFactor's logging + readouts. Personal use (me, spouse later). I control the data, the matching logic, and the dashboards.

**Changes from v2** (planning session, July 2026): identity column from day one (single user in v1); bulk FDC import instead of lazy-only; barcode scanning de-risked in Phase 0; iOS-correct offline queue (IndexedDB outbox, not Background Sync); client-supplied log dates; local-only search-as-you-type; explicit migrate command; defined coverage formula; new dashboard-integration phase (dashboard reads `food_log` rollups); MCP split into stdio-first then remote OAuth.

## Context for Claude Code

- I already run custom Python MCP servers (Hevy, MacroFactor), so assume comfort with Python, FastAPI-style services, and MCP.
- **Deployment: Railway** (same account as my existing health website service). Public URL, so the API must be authenticated.
- **Database: Neon Postgres** — the same Neon instance that already holds my health-import data. The food logger gets its own `food_log` schema. Do NOT touch existing tables outside that schema. Never run destructive SQL against other schemas.
- Phone client is a PWA used in Chrome on iOS (WebKit underneath) — installed to home screen. All PWA features must be verified against iOS WebKit specifically, not desktop Chrome.
- All body weights displayed in **pounds**. Nutrients per 100g internally; grams are the canonical logging unit.
- Schema file `db/food_log_schema.pg.sql` is the source of truth. Apply it as migration 0001; subsequent changes are numbered plain-SQL migration files. **Migrations run only via an explicit `migrate` command** (with a `schema_migrations` tracking table) — never automatically on app startup against the shared Neon instance.

## Tech stack (decided — don't relitigate)

- **Backend:** Python 3.12, FastAPI, `asyncpg` or `psycopg3` against Neon. No heavy ORM; plain SQL or a thin query layer.
- **Auth:** Single static bearer token from env (`API_TOKEN`), required on every endpoint except `/health`. The PWA stores it after a one-time entry screen. Token maps server-side to an identity (see Identity below).
- **Frontend:** Single-page PWA, Vite build, Preact/React fine. `@zxing/browser` or `html5-qrcode` for barcode scanning (winner chosen by the Phase 0 spike).
- **Food data:** USDA FoodData Central (bulk CSV import + API), Open Food Facts (barcode fallback). Keys/secrets in Railway env vars; `.env.example` in repo.
- **Search:** Postgres full-text (`search_vec` GIN index) with `pg_trgm` trigram fallback for typos.
- **Testing:** pytest; test matching/conversion/rollup/coverage math hard, skim the CRUD.

## Cross-cutting design decisions (settled in planning — bake into schema + code)

1. **Identity from day one, single user in v1.** Every `log_entries`, `body_weight`, and `targets` row carries `logged_by TEXT NOT NULL` (default `'carlos'`). v1 ships one token → one identity; adding my spouse later is a second token + identity mapping, zero data migration. No per-user UI in v1.
2. **Date attribution is client-owned.** The PWA sends the local calendar date with every log/weight entry; the server stores it verbatim (plus a UTC `logged_at` timestamp for audit). An 11pm snack lands on today, never tomorrow. No server-side UTC date derivation anywhere.
3. **Search-as-you-type is local-only.** Keystroke queries hit Postgres FTS/trigram exclusively. Live FDC search is an explicit user action ("Search USDA →"), debounced, importing hits on the fly. This keeps us far from FDC's ~1,000 req/hr limit.
4. **Coverage formula (pin with tests):** for nutrient *k* over a period, `coverage = grams logged from foods reporting k ÷ total grams logged`. Displayed alongside every micronutrient total so a low number is distinguishable from unlogged data.
5. **Offline queue is an IndexedDB outbox.** iOS WebKit does not support the Background Sync API. Failed log POSTs queue in IndexedDB and retry on app open / foreground / manual refresh. Service worker caches the app shell only.
6. **Dashboard integration direction (decided):** my existing health dashboard will *read* daily rollups from the `food_log` schema (view or API — chosen in Phase 6). The logger never writes outside its own schema. Until cutover, MacroFactor keeps feeding the dashboard; avoid double-counting by not wiring the dashboard to `food_log` until MacroFactor logging stops.

## Repository layout

```
food-logger/
  backend/
    app/           # FastAPI app: routers, services, db
    import/        # FDC bulk + API import, OFF lookup
    tests/
  frontend/        # PWA (Vite project)
  db/
    food_log_schema.pg.sql   # migration 0001 (source of truth)
    migrations/
  mcp/             # MCP server exposing the log to Claude
  railway.json / Procfile as appropriate
```

## Schema design (to be encoded as `db/food_log_schema.pg.sql` — first coding deliverable)

All tables in schema `food_log`.

- **foods** — id, source (`fdc_foundation` | `fdc_sr_legacy` | `fdc_branded` | `off` | `custom`), source_id, name, brand, barcode (nullable, indexed), search_vec (tsvector, GIN), source_payload JSONB (raw upstream response), created/updated timestamps. Unique on (source, source_id).
- **portions** — id, food_id FK, description ("1 cup", "1 slice"), gram_weight. Raw grams is always available as an implicit portion.
- **nutrients** — food_id FK, nutrient_key (snake_case, e.g. `magnesium_mg`), amount_per_100g. PK (food_id, nutrient_key). Import EVERY nutrient the source reports.
- **log_entries** — id, logged_by, date (client-local), meal (`breakfast`|`lunch`|`dinner`|`snack`), food_id FK, grams (canonical), portion_id + portion_qty (nullable, for display/re-edit), logged_at timestamptz, client_id (UUID from the PWA for idempotent retry from the outbox).
- **body_weight** — id, logged_by, date, weight_lb, logged_at. Unique (logged_by, date).
- **targets** — logged_by, effective_date, kcal, protein_g, carbs_g, fat_g, fiber_g (+ optional sodium_mg). PK (logged_by, effective_date); current target = latest effective_date ≤ today.
- **schema_migrations** — filename, applied_at.

Idempotency rule (mirrors my dashboard's hard-won convention): every upsert keyed on a natural key — foods on (source, source_id), log POSTs on client_id — so imports and outbox retries are always safe to replay.

## Phases — build in order, each phase ends working and tested

### Phase 0 — Scaffold, deploy skeleton, **and the barcode spike**
- Repo layout above; `.env.example` (DATABASE_URL, FDC_API_KEY, API_TOKEN, PORT); README with local-run and Railway deploy instructions.
- Write `db/food_log_schema.pg.sql`; `migrate` command applies it (tracked in `schema_migrations`).
- Bearer-token middleware from day one; `/health` open, everything else 401s.
- Deploy to Railway as a new service; confirm it reaches Neon.
- **Barcode spike (gate for Phase 4 design):** throwaway page served by the skeleton — camera preview + `@zxing/browser` and/or `html5-qrcode` decode — installed to my iPhone home screen and tested on a real product barcode. This is the riskiest tech in the plan (no `BarcodeDetector` in WebKit; camera-in-standalone-PWA quirks); prove it before building on it. Record which library won and any iOS workarounds in the README.
- **Done when:** the Railway URL answers `/health`, an authed request works, an unauthed one is rejected, the `food_log` schema exists in Neon, and a barcode scans successfully from the home-screen PWA on my phone.

### Phase 1 — Food data layer (bulk-first)
- **Bulk import:** `import/fdc_bulk.py` ingests the FDC Foundation + SR Legacy CSV downloads (~15k generic foods) into `foods`/`portions`/`nutrients`, normalized per-100g, every nutrient, snake_cased keys. Local search is useful from day one.
- `import/fdc_import.py`: given an FDC ID or search term, fetch from the FDC API and upsert (Foundation, SR Legacy, Branded; branded carries `barcode`). Store raw response in `source_payload`.
- `import/off_lookup.py`: given a barcode, query Open Food Facts, normalize per-100g (serving-size quirks, kJ→kcal), upsert with whatever micros the label reports.
- Barcode resolution order: local DB → OFF → FDC branded search. Cache everything locally on first hit.
- When FDC/OFF data is ambiguous (serving sizes especially), normalize conservatively, keep the raw JSON, and flag oddities in comments rather than guessing silently.
- **Done when:** bulk import populates ~15k foods with correct per-100g rows; one command with a barcode or search term produces correct rows. Unit tests cover nutrient normalization edge cases (kJ→kcal, serving-basis vs 100g-basis, label rounding).

### Phase 2 — Search & matching API
- `GET /foods/search?q=` → local FTS ranked by `ts_rank`, trigram fallback when FTS returns nothing. **No implicit live-FDC fallthrough** — response includes a flag the UI uses to offer explicit "Search USDA →" (`GET /foods/search?q=&remote=1`), which imports hits on the fly.
- `GET /foods/barcode/{code}` → resolution chain from Phase 1.
- `GET /foods/{id}` → food + portions + nutrients.
- `POST /foods` → custom food creation.
- **Done when:** "greek yogurt" returns sane ranked results fast from the bulk-imported set; "yogrt" still finds yogurt; an unknown barcode gets fetched, cached, and returned; remote search only fires when asked.

### Phase 3 — Logging API
- `POST /log` (client_id, logged_by-implied-by-token, client-local date, meal, food_id, grams OR portion_id+qty — server converts to canonical grams; idempotent on client_id), `GET /log/{date}`, `PATCH /log/{id}`, `DELETE /log/{id}`.
- `GET /summary/{date}` and `GET /summary?start=&end=` → daily macro rollups (kcal, protein, carbs, fat, fiber).
- `GET /summary/nutrient/{key}?start=&end=` → daily totals for any nutrient key **plus the coverage figure** (formula above).
- `POST /weight`, `GET /weight?start=&end=` — lbs in, lbs out.
- `GET/PUT /targets` — versioned by effective_date.
- **Done when:** full log-a-day flow works via authed curl; replaying the same POST (same client_id) doesn't duplicate; rollup and coverage math verified by tests.

### Phase 4 — PWA frontend (logging-first, thumb-friendly)
This is the whole reason the project exists — MacroFactor's readout is bad; mine must be fast and legible.
- **First-run screen:** paste API token once; stored locally.
- **Log screen (default): recents-first.** The primary interaction is the recent-foods list (tap to re-log with last portion) — that, not search, is what wins the 15-second goal. Then: big search box (as-you-type = local only), barcode scan button (per Phase 0 spike findings), explicit "Search USDA →" escalation. Selecting a food shows portion picker (portions from DB + raw grams) with live macro preview.
- **Day view:** entries grouped by meal, running totals vs. targets, remaining macros prominent.
- **Trends view:** 7/30-day kcal + protein charts; weight trend (lbs) with 7-day smoothing overlay; a micronutrient panel (pick a nutrient → daily bars with coverage shading).
- PWA requirements: manifest + icons, service worker caching the shell, **IndexedDB outbox** for failed log POSTs (retry on open/foreground — no Background Sync on iOS), installable from Chrome on iOS.
- **Done when:** I can log breakfast from my home screen in under 15 seconds including a barcode scan, and a log made in airplane mode lands once I'm back online.

### Phase 5 — MCP server (stdio first)
- `mcp/` exposes: search_foods, log_food, get_day_summary, get_trends, get_nutrient_summary, log_weight — thin wrappers over the API using the bearer token.
- **5a: stdio mode** for Claude Code — trivial, ship first.
- **5b: remote hosting** alongside the Railway service for claude.ai. Note: claude.ai custom connectors authenticate via OAuth, not static bearer headers — scope the OAuth wrapper as its own step (mirroring what I explored for Hevy/MacroFactor), don't let it block 5a.
- **Done when:** Claude can log a meal and pull a weekly summary through MCP from Claude Code (5a); remote (5b) tracked separately.

### Phase 6 — Dashboard cutover + nice-to-haves (only after 0–5 are solid)
- **Dashboard integration (decided direction, do first):** my health dashboard reads daily rollups from `food_log` — either a read-only SQL view (`food_log.daily_summary`) it queries directly, or its server calls `GET /summary`. The dashboard's `nutrition_days`/HAE-webhook path is retired at the same moment MacroFactor logging stops — never both live, to avoid double-counting. (The dashboard-side change happens in the Claudeai repo, referencing this contract.)
- Apple Health ingestion: body weight flows into `food_log.body_weight` from the existing Neon health-import pipeline rather than a parallel path.
- Natural-language logging endpoint: free text → Claude API → structured entries against local DB (confirm-before-save UX).
- Meal templates ("my usual breakfast") — cheap win on the 15-second goal.
- Recipes table: composite foods built from `foods` rows.
- TDEE estimation: weight-trend smoothing + intake regression (design doc first, don't freestyle the math).

## Non-goals (v1)
- User accounts / OAuth for the app itself (static bearer token is the perimeter; identity column is future-proofing, not a user system).
- Spouse onboarding (second token + identity) — schema-ready, not built.
- Micronutrient completeness guarantees — the coverage metric exists to make gaps visible, not to fix them.
- App Store anything; offline-first sync beyond the outbox retry.

## Working agreements
- Small commits per phase step; each phase merges only when its "done when" passes.
- Ask before adding dependencies beyond the stack above.
- Never run destructive SQL against Neon schemas other than `food_log`.
- iOS WebKit is the reference browser for every frontend claim — "works in Chrome desktop" proves nothing here.
