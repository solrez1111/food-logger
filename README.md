# food-logger

Self-hosted food logging app replacing MacroFactor's logging and readouts. Personal use.

- **Backend:** Python 3.12 + FastAPI, plain SQL against Neon Postgres (dedicated `food_log` schema)
- **Frontend:** installable PWA (Vite), used in Chrome on iOS — barcode scan, recents-first logging
- **Food data:** USDA FoodData Central (bulk import + API), Open Food Facts barcode fallback
- **Deploy:** Railway; API secured by static bearer token, `/health` public
- **MCP:** server exposing search/log/summary tools to Claude

**Read [`PLAN.md`](./PLAN.md) before writing any code.** It is the source of truth for scope, phases, schema design, cross-cutting decisions, and working agreements. Each phase ends working and tested per its "done when" criteria.

## Status

Phase 0 not started. Nothing is built yet.

## Setup (once Phase 0 lands)

```bash
cp .env.example .env   # DATABASE_URL, FDC_API_KEY, API_TOKEN, PORT
# backend and frontend run instructions land with Phase 0
```
