# Food Logger — Implementation Plan (v3.3: Railway + Neon)

A self-hosted food logging app to replace MacroFactor's logging + readouts. Personal use (me, spouse later). I control the data, the matching logic, and the dashboards.

**Changes from v2** (planning session, July 2026): identity column from day one (single user in v1); bulk FDC import instead of lazy-only; barcode scanning de-risked in Phase 0; iOS-correct offline queue (IndexedDB outbox, not Background Sync); client-supplied log dates; local-only search-as-you-type; explicit migrate command; defined coverage formula; new dashboard-integration phase (dashboard reads `food_log` rollups); MCP split into stdio-first then remote OAuth.

**Changes in v3.1:** identity is a `users` table + `user_id` FK from day one (not a free-text column); token→user resolution isolated in a single swappable `get_current_user` dependency; added the "Scaling to a few users" appendix (future work, deliberately not v1).

**Changes in v3.2** (final pre-build Q&A): NO meal concept — a day is one chronological list (meal column dropped); sodium is a first-class daily target (hypertension is the whole point); AI plate estimation promoted from nice-to-have to its own Phase 5 — it is how I log plated meals in MacroFactor today; manual entry is portion-picker-first with grams one tap behind; no MacroFactor history import — starting fresh.

**Changes in v3.3:** photo is the PRIMARY logging mode, in Phase 5 from day one (my real MacroFactor usage: photo almost always, text-describe when I forgot to shoot, favorites for staples) — not a future upgrade; text-describe is the fallback path on the same endpoint/UX; favorites (food + usual portion, one tap to log) added to v1 schema, API, and log screen.

## Context for Claude Code

- I already run custom Python MCP servers (Hevy, MacroFactor), so assume comfort with Python, FastAPI-style services, and MCP.
- **Deployment: Railway** (same account as my existing health website service). Public URL, so the API must be authenticated.
- **Database: Neon Postgres** — the same Neon instance that already holds my health-import data. The food logger gets its own `food_log` schema. Do NOT touch existing tables outside that schema. Never run destructive SQL against other schemas.
- Phone client is a PWA used in Chrome on iOS (WebKit underneath) — installed to home screen. All PWA features must be verified against iOS WebKit specifically, not desktop Chrome.
- All body weights displayed in **pounds**. Nutrients per 100g internally; grams are the canonical logging unit.
- Schema file `db/food_log_schema.pg.sql` is the source of truth. Apply it as migration 0001; subsequent changes are numbered plain-SQL migration files. **Migrations run only via an explicit `migrate` command** (with a `schema_migrations` tracking table) — never automatically on app startup against the shared Neon instance.

## Tech stack (decided — don't relitigate)

- **Backend:** Python 3.12, FastAPI, `asyncpg` or `psycopg3` against Neon. No heavy ORM; plain SQL or a thin query layer.
- **Auth:** Single static bearer token from env (`API_TOKEN`), required on every endpoint except `/health`. The PWA stores it after a one-time entry screen. All token→user resolution lives in ONE FastAPI dependency (`get_current_user`) that every router uses — in v1 it just checks the static token and returns user 1, but it is the single seam where real multi-user auth would slot in later (see appendix). No route reads the token directly.
- **Frontend:** Single-page PWA, Vite build, Preact/React fine. `@zxing/browser` or `html5-qrcode` for barcode scanning (winner chosen by the Phase 0 spike).
- **Food data:** USDA FoodData Central (bulk CSV import + API), Open Food Facts (barcode fallback). Keys/secrets in Railway env vars; `.env.example` in repo.
- **Search:** Postgres full-text (`search_vec` GIN index) with `pg_trgm` trigram fallback for typos.
- **Testing:** pytest; test matching/conversion/rollup/coverage math hard, skim the CRUD.

## Cross-cutting design decisions (settled in planning — bake into schema + code)

1. **Identity from day one, single user in v1.** A real `users` table exists from migration 0001, seeded with one row (me). Every `log_entries`, `body_weight`, and `targets` row carries `user_id NOT NULL REFERENCES users(id)`. v1 ships one token → user 1 via `get_current_user`; adding my spouse later is a new user row + second token, zero data migration. No per-user UI in v1.
2. **Date attribution is client-owned.** The PWA sends the local calendar date with every log/weight entry; the server stores it verbatim (plus a UTC `logged_at` timestamp for audit). An 11pm snack lands on today, never tomorrow. No server-side UTC date derivation anywhere.
3. **Search-as-you-type is local-only.** Keystroke queries hit Postgres FTS/trigram exclusively. Live FDC search is an explicit user action ("Search USDA →"), debounced, importing hits on the fly. This keeps us far from FDC's ~1,000 req/hr limit.
4. **Coverage formula (pin with tests):** for nutrient *k* over a period, `coverage = grams logged from foods reporting k ÷ total grams logged`. Displayed alongside every micronutrient total so a low number is distinguishable from unlogged data.
5. **Offline queue is an IndexedDB outbox.** iOS WebKit does not support the Background Sync API. Failed log POSTs queue in IndexedDB and retry on app open / foreground / manual refresh. Service worker caches the app shell only.
6. **Dashboard integration direction (decided):** my existing health dashboard will *read* daily rollups from the `food_log` schema (view or API — chosen in Phase 7). The logger never writes outside its own schema. Until cutover, MacroFactor keeps feeding the dashboard; avoid double-counting by not wiring the dashboard to `food_log` until MacroFactor logging stops.
7. **No meal concept.** A day is a single chronological list of entries — no breakfast/lunch/dinner/snack column, grouping, or picker anywhere (schema, API, UI). Fewer taps per log; `logged_at` preserves ordering.
8. **Sodium is a first-class target.** `sodium_mg` sits in the targets table and the daily rollup at the same tier as protein, with a prominent running total / remaining in Day view. Always show its coverage figure — branded foods report sodium inconsistently, and an understated sodium number is worse than none (hypertension management is the reason this app exists).
9. **Starting fresh — no MacroFactor history import.** Trends build from the first logged day. Consequence: TDEE estimation (Phase 7) has no data until weeks of logging accumulate; its charts must show honest "still collecting (n=X days)" states rather than confident numbers from thin data.
10. **Photo is the primary logging mode.** The logging hierarchy, by actual frequency of use: (1) photo of the plate → AI estimate, (2) text describe when there's no photo, (3) one-tap favorites for staples, (4) barcode for packaged foods, (5) manual search as the floor. The camera button gets the most prominent placement on the log screen, and the photo path is optimized hardest for speed. Photo and text are the same endpoint and the same confirm-before-save UX — only the input differs.

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

- **users** — id, name, created_at. Seeded with row 1 (me) in migration 0001. No credentials stored in v1 (the static token maps to user 1 in code); columns for auth arrive only if the appendix work ever happens.
- **foods** — id, source (`fdc_foundation` | `fdc_sr_legacy` | `fdc_branded` | `off` | `custom`), source_id, name, brand, barcode (nullable, indexed), search_vec (tsvector, GIN), source_payload JSONB (raw upstream response), created/updated timestamps. Unique on (source, source_id).
- **portions** — id, food_id FK, description ("1 cup", "1 slice"), gram_weight. Raw grams is always available as an implicit portion.
- **nutrients** — food_id FK, nutrient_key (snake_case, e.g. `magnesium_mg`), amount_per_100g. PK (food_id, nutrient_key). Import EVERY nutrient the source reports.
- **log_entries** — id, user_id FK, date (client-local), food_id FK, grams (canonical), portion_id + portion_qty (nullable, for display/re-edit), logged_at timestamptz, client_id (UUID from the PWA for idempotent retry from the outbox), entry_method (`manual`|`barcode`|`favorite`|`ai_photo`|`ai_text`|`mcp` — lets us audit how AI-estimated entries compare later). No meal column (decision 7).
- **favorites** — id, user_id FK, food_id FK, default_grams (or portion_id + qty), label (optional override, "morning yogurt"), position. One tap on the log screen logs the food at its usual amount (decision 10).
- **body_weight** — id, user_id FK, date, weight_lb, logged_at. Unique (user_id, date).
- **targets** — user_id FK, effective_date, kcal, protein_g, carbs_g, fat_g, fiber_g, sodium_mg (first-class, decision 8). PK (user_id, effective_date); current target = latest effective_date ≤ today.
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

- **✅ SPIKE OUTCOME (July 2026, iOS 18.7 / WebKit 26.4, standalone PWA):** live camera in a home-screen PWA works. **ZXing wins** — read UPC-A / EAN-13 / EAN-8 product codes accurately; **html5-qrcode produced no reads** for 1D barcodes and is dropped. The friction is *aiming*, not decode (once framed, reads are instant; the spike's large "ms after start" values were camera-alignment time, not compute). **Phase 4 barcode UX must therefore ship:** a targeting reticle to frame the code, tap-to-focus, a torch/flashlight toggle for low light, and continuous scan with the haptic confirmation the spike already proved. Barcode is only path #4 (photo → describe → favorites → barcode), so "good enough" is the bar and it's met. Phase 0 COMPLETE.

### Phase 1 — Food data layer (bulk-first)
- **Bulk import:** `import/fdc_bulk.py` ingests the FDC Foundation + SR Legacy CSV downloads (~15k generic foods) into `foods`/`portions`/`nutrients`, normalized per-100g, every nutrient, snake_cased keys. Local search is useful from day one.
- `import/fdc_import.py`: given an FDC ID or search term, fetch from the FDC API and upsert (Foundation, SR Legacy, Branded; branded carries `barcode`). Store raw response in `source_payload`.
- `import/off_lookup.py`: given a barcode, query Open Food Facts, normalize per-100g (serving-size quirks, kJ→kcal), upsert with whatever micros the label reports.
- Barcode resolution order: local DB → OFF → FDC branded search. Cache everything locally on first hit.
- When FDC/OFF data is ambiguous (serving sizes especially), normalize conservatively, keep the raw JSON, and flag oddities in comments rather than guessing silently.
- **Done when:** bulk import populates ~15k foods with correct per-100g rows; one command with a barcode or search term produces correct rows. Unit tests cover nutrient normalization edge cases (kJ→kcal, serving-basis vs 100g-basis, label rounding).

- **✅ COMPLETE (July 2026):** bulk import loaded **8,204 foods** into Neon (411 Foundation + 7,793 SR Legacy — the "~15k" estimate was high; Foundation is a small curated set). Per-100g nutrients, portions, FTS/trigram indexes all populated. 36 unit/integration tests green (kJ→kcal precedence, salt→sodium, serving-basis, barcode variants, idempotent upsert). Importers: `fdc_bulk.py` (CSV), `fdc_import.py` (API), `off_lookup.py` (barcode chain). Phase 1 DONE.

### Phase 2 — Search & matching API
- `GET /foods/search?q=` → local FTS ranked by `ts_rank`, trigram fallback when FTS returns nothing. (Verified on PG16 in Phase 0: rank the fallback by `word_similarity(q, name)` with a ~0.4 cutoff — the `<%` operator's default 0.6 threshold misses obvious typos: 'yogrt' vs 'Greek Yogurt Plain' scores 0.5.) **No implicit live-FDC fallthrough** — response includes a flag the UI uses to offer explicit "Search USDA →" (`GET /foods/search?q=&remote=1`), which imports hits on the fly.
- `GET /foods/barcode/{code}` → resolution chain from Phase 1.
- `GET /foods/{id}` → food + portions + nutrients.
- `POST /foods` → custom food creation.
- **Done when:** "greek yogurt" returns sane ranked results fast from the bulk-imported set; "yogrt" still finds yogurt; an unknown barcode gets fetched, cached, and returned; remote search only fires when asked.

- **✅ BUILT (July 2026):** all four endpoints live behind the bearer perimeter, with per-result macro previews (kcal/protein/carbs/fat/sodium per 100g) and an `offer_remote` flag when local results are weak. Verified: FTS ranking, trigram typo rescue ('brocoli' → 'Broccoli, raw'), UPC-A↔EAN-13 barcode variant matching, custom-food validation. 44 tests green. Remaining for DONE: prod smoke against the full 8,204-food catalog + one unknown-barcode fetch through OFF (operator console).

### Phase 3 — Logging API
- `POST /log` (client_id, user implied by token via `get_current_user`, client-local date, food_id, grams OR portion_id+qty — server converts to canonical grams; idempotent on client_id), `GET /log/{date}`, `PATCH /log/{id}`, `DELETE /log/{id}`.
- `GET /summary/{date}` and `GET /summary?start=&end=` → daily rollups (kcal, protein, carbs, fat, fiber, **sodium_mg with its coverage figure** — sodium is target-tier, decision 8).
- `GET /summary/nutrient/{key}?start=&end=` → daily totals for any nutrient key **plus the coverage figure** (formula above).
- `POST /weight`, `GET /weight?start=&end=` — lbs in, lbs out.
- `GET/PUT /targets` — versioned by effective_date.
- `GET/POST/DELETE /favorites` — food + usual amount; `POST /log` accepts a favorite_id shorthand.
- **Done when:** full log-a-day flow works via authed curl; replaying the same POST (same client_id) doesn't duplicate; rollup and coverage math verified by tests.

- **✅ BUILT (July 2026):** all endpoints live behind the bearer perimeter. Coverage math pinned by tests (sodium coverage 2/3 with a non-reporting food in the mix; unreported nutrients return null + 0.0 coverage, never fake zeros). client_id replay verified idempotent; portion→grams conversion server-side; favorites log in one tap with entry_method='favorite'; targets versioned by effective_date with remaining computed in the day summary. Full log-a-day flow driven via authed curl locally. 58 tests green.

### Phase 4 — PWA frontend (logging-first, thumb-friendly)
This is the whole reason the project exists — MacroFactor's readout is bad; mine must be fast and legible.
- **First-run screen:** paste API token once; stored locally.
- **Log screen (default), ordered by decision 10's hierarchy:** a big camera button at the top (wired fully in Phase 5 — position and prominence designed now), then favorites (one tap logs at usual amount) and recents (tap to re-log with last portion), then search box (as-you-type = local only), barcode scan button, explicit "Search USDA →" escalation. Favorites are managed in place (long-press a recent/search result → "add to favorites" with amount).
- **Barcode scanner (ZXing, per Phase 0 spike):** the spike proved decode works but aiming is the friction. Ship a targeting reticle to frame the code, tap-to-focus, and a torch/flashlight toggle for low light; continuous scan with haptic confirmation on hit. Keep it lightweight — barcode is path #4, not the star.
- **Amount entry: portion-picker-first, grams one tap behind** (my MacroFactor habit). Selecting a food leads with its portions ("1 cup", "1 container"); a visible toggle switches to raw-gram entry. Live macro + sodium preview either way.
- **Day view:** single chronological list (no meal grouping, decision 7); running totals vs. targets with remaining kcal/protein/**sodium** prominent.
- **Trends view:** 7/30-day kcal + protein charts; weight trend (lbs) with 7-day smoothing overlay; a micronutrient panel (pick a nutrient → daily bars with coverage shading).
- PWA requirements: manifest + icons, service worker caching the shell, **IndexedDB outbox** for failed log POSTs (retry on open/foreground — no Background Sync on iOS), installable from Chrome on iOS.
- **Done when:** I can log breakfast from my home screen in under 15 seconds including a barcode scan, a favorite logs in one tap, and a log made in airplane mode lands once I'm back online.

- **✅ BUILT (July 2026):** full PWA in `frontend/` (Vite+React, no router/chart deps — hand-rolled SVG charts), served by FastAPI with SPA fallback; Railway builds it via nixpacks (python312+nodejs_22). Log screen in decision-10 order with camera placeholder for Phase 5; ZXing scanner with reticle + torch + haptics per the spike findings; portion-first sheet with live preview and save-as-favorite; day view leads with remaining kcal/sodium incl. coverage notes; trends with coverage-shaded nutrient bars; SW shell cache + IndexedDB outbox. Backend gained GET /api/log/recent. Whole flow driven in headless Chromium (token → search → log → day → trends), zero JS errors. Remaining for DONE: the on-phone checks (install, 15-second breakfast incl. scan, airplane-mode outbox landing) — operator steps.

### Phase 5 — AI plate estimation, photo-first (core — this is my primary logging mode)
- **Photo path (primary, optimize hardest):** the log screen's camera button → iOS camera via `<input type="file" accept="image/*" capture="environment">` (native still-photo capture works reliably in iOS WebKit — this is NOT the risky getUserMedia path the barcode spike de-risks). Client downscales/re-encodes to ~1024px JPEG before upload (iPhone HEIC originals are huge; canvas re-encode handles the format and keeps the round trip fast and the vision call cheap). Target: tap camera → shoot → candidates on screen in a few seconds.
- **Text path (fallback, same pipeline):** "describe instead" on the same screen for when I forgot to shoot — free-text description, dictation-friendly. Optionally both: a photo plus a clarifying note ("the sauce is Greek yogurt").
- `POST /log/estimate` accepts image and/or text → Claude vision call → structured candidate entries: each matched to a **local DB food** (FTS over the bulk-imported set; never invented nutrition data) with an estimated gram amount and the model's reasoning ("palm-size chicken ≈ 120g").
- **Confirm-before-save UX, always.** The PWA shows the candidate list with per-item portion/gram adjusters and live totals; nothing writes to `log_entries` until I confirm. Saved entries carry `entry_method='ai_photo'` or `'ai_text'`.
- Unmatched items fall back to the explicit remote-search flow rather than silently guessing; the response marks low-confidence estimates so the UI can flag them.
- Server-side only (`ANTHROPIC_API_KEY` in Railway env — add to `.env.example`); use a fast vision-capable model (Haiku-class) — this runs at mealtimes and must feel instant. Photos are processed for estimation, not stored (no image persistence in v1).
- **Done when:** photographing a real dinner from the home-screen PWA produces sensible matched foods + gram estimates I can adjust and confirm faster than manual entry; the text path works when no photo exists; estimation failures degrade gracefully to manual search.

- **✅ BUILT (July 2026):** POST /api/log/estimate (photo and/or text → Claude Haiku vision via forced tool-use → candidates matched through app.search against the local catalog; nutrition only ever from the catalog). Camera button wired via native photo input with client-side ≤1024px JPEG re-encode; describe-instead sheet; confirm-before-save EstimateSheet with per-row grams adjusters, alternative-match dropdowns, inline manual search for unmatched rows, live kcal/protein/sodium totals, skip/include toggles; entries tagged ai_photo/ai_text; offline confirm falls into the outbox. Photos not persisted. 69 backend tests (Claude call mocked); full photo→confirm→day-view flow driven in headless Chromium, zero JS errors. Remaining for DONE: real-dinner photo on the phone with ANTHROPIC_API_KEY set (operator).

### Phase 6 — MCP server (stdio first)
- `mcp/` exposes: search_foods, log_food, estimate_plate, get_day_summary, get_trends, get_nutrient_summary, log_weight — thin wrappers over the API using the bearer token.
- **6a: stdio mode** for Claude Code — trivial, ship first.
- **6b: remote hosting** alongside the Railway service for claude.ai. Note: claude.ai custom connectors authenticate via OAuth, not static bearer headers — scope the OAuth wrapper as its own step (mirroring what I explored for Hevy/MacroFactor), don't let it block 6a.
- **Done when:** Claude can log a meal and pull a weekly summary through MCP from Claude Code (6a); remote (6b) tracked separately.

- **✅ BUILT (July 2026):** seven tools (search_foods, log_food, estimate_plate, get_day_summary, get_trends, get_nutrient_summary, log_weight) as thin API wrappers in backend/app/mcp_server.py. BOTH transports shipped: stdio launcher (mcp/server.py, 6a) and remote streamable HTTP mounted at /mcp/{API_TOKEN} inside the Railway service — a 6b-lite secret-path perimeter so claude.ai connects today; full OAuth remains 6b. Verified end-to-end with a real MCP client over both transports: search → log (entry_method='mcp', correct macros) → day summary → weight → sodium trend; estimate degrades cleanly without ANTHROPIC_API_KEY. estimate_plate's docstring enforces confirm-before-save. TZ env drives client-local "today".

### Phase 7 — Dashboard cutover + nice-to-haves (only after 0–6 are solid)
- **Dashboard integration (decided direction, do first):** my health dashboard reads daily rollups from `food_log` — either a read-only SQL view (`food_log.daily_summary`) it queries directly, or its server calls `GET /summary`. The dashboard's `nutrition_days`/HAE-webhook path is retired at the same moment MacroFactor logging stops — never both live, to avoid double-counting. (The dashboard-side change happens in the Claudeai repo, referencing this contract.)
- Apple Health ingestion: body weight flows into `food_log.body_weight` from the existing Neon health-import pipeline rather than a parallel path.
- Saved combos ("my usual breakfast" — one tap logs several foods at once; extends Phase 4's single-food favorites).
- Recipes table: composite foods built from `foods` rows.
- Optional photo persistence for AI-estimated entries (thumbnail on the day view as a visual food diary; v1 discards photos after estimation).
- TDEE estimation: weight-trend smoothing + intake regression (design doc first, don't freestyle the math). Note decision 9: starting fresh means this has nothing to compute for the first several weeks — show "still collecting (n=X days)", never a confident number from thin data.

## Non-goals (v1)
- User accounts / OAuth for the app itself (static bearer token is the perimeter; the `users` table and `user_id` columns are future-proofing, not a user system).
- Spouse onboarding (second token + user row) — schema-ready, not built.
- Micronutrient completeness guarantees — the coverage metric exists to make gaps visible, not to fix them.
- MacroFactor history import — starting fresh (decision 9).
- App Store anything; offline-first sync beyond the outbox retry.

## Working agreements
- Small commits per phase step; each phase merges only when its "done when" passes.
- Ask before adding dependencies beyond the stack above.
- Never run destructive SQL against Neon schemas other than `food_log`.
- iOS WebKit is the reference browser for every frontend claim — "works in Chrome desktop" proves nothing here.

## Appendix: scaling to a few users (future — do NOT build in v1)

Captured July 2026 so the upgrade path is designed, not improvised. "A few users" means ~3–20 trusted people (friends/family), not a public product. The stack itself already scales to that: one FastAPI instance + Postgres handles dozens of users; the food catalog, bulk import, and rollup math are user-count-independent. The work is entirely auth, isolation, and hygiene:

1. **Real login replaces static tokens.** Static bearer tokens have no self-service onboarding and no revocation short of a redeploy. Replace with email magic links (least effort — no stored passwords) or passkeys (nicest on iOS). Adds a `sessions`/`api_tokens` table and credential columns on `users`. Because v1 isolates all auth in `get_current_user`, this replaces one function, not every router.
2. **User scoping becomes a security boundary, not just attribution.** Every query on log_entries / summaries / body_weight / targets / recents must filter by the authenticated user, enforced in ONE place (query-layer guard or Postgres RLS), with tests asserting user A cannot read user B's rows. One forgotten WHERE clause = someone else's weight history. This is health-adjacent data; isolation is the feature.
3. **Custom foods get ownership + visibility.** `POST /foods` entries become private-by-default (`created_by`, `visibility: private|shared`) so one person's "Mom's casserole" doesn't pollute everyone's search. Catalog foods (FDC/OFF) stay global.
4. **Move off the shared Neon instance.** With outsiders' data in play, the logger gets its own Neon database — the blast radius of a bad migration must not include my personal health-import data. Decided early, it's a one-line DATABASE_URL change.
5. **Operational hygiene becomes non-optional.** Rate limiting on auth endpoints, token revocation, error tracking (e.g. Sentry), real backups. Per-user tokens flow through to MCP: each person's Claude connects with their own credential (folds into Phase 6b's OAuth work). AI plate estimation also needs a per-user rate cap once outsiders can trigger Anthropic API spend.

Rough size: about one full phase of work. v1 deliberately pre-pays only the cheap parts (users table, user_id FKs, the `get_current_user` seam); everything else waits until there's a real second household asking.
