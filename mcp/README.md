# food-log MCP server

Exposes the food log to Claude: `search_foods`, `log_food`, `estimate_plate`
(text → candidates; confirm with the user before logging), `get_day_summary`,
`get_trends`, `get_nutrient_summary` (any stored nutrient key), `log_weight`.

Tools are thin wrappers over the HTTP API — auth, validation, idempotency, and
rollup logic all live server-side. Logged entries carry `entry_method='mcp'`.

## claude.ai (remote, streamable HTTP)

The MCP server is mounted inside the Railway service at:

```
https://<your-app>.up.railway.app/mcp/<API_TOKEN>
```

claude.ai → Settings → Connectors → **Add custom connector** → paste that URL.

**The URL embeds your API token — treat it like a password.** This is the
pragmatic Phase 6b-lite perimeter for a single-user app; full OAuth is tracked
as Phase 6b proper. Rotating `API_TOKEN` in Railway rotates the URL (and logs
out the PWA, which will re-ask for the token).

## Claude Code / Claude Desktop (stdio)

```bash
pip install -r backend/requirements.txt   # mcp + httpx

claude mcp add food-log \
  -e FOOD_LOG_API_URL=https://<your-app>.up.railway.app \
  -e FOOD_LOG_API_TOKEN=<your API_TOKEN> \
  -e FOOD_LOG_TZ=America/New_York \
  -- python3 /path/to/food-logger/mcp/server.py
```

`FOOD_LOG_TZ` matters: it defines "today" for logging (client-local date
attribution). Defaults to America/New_York.
