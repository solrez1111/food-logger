#!/usr/bin/env python3
"""stdio launcher for the food-log MCP server (Claude Code / Claude Desktop).

Register with Claude Code:
    claude mcp add food-log \
      -e FOOD_LOG_API_URL=https://<your-app>.up.railway.app \
      -e FOOD_LOG_API_TOKEN=<your API_TOKEN> \
      -e FOOD_LOG_TZ=America/New_York \
      -- python3 /path/to/food-logger/mcp/server.py

Requires: pip install -r backend/requirements.txt (mcp + httpx).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.mcp_server import build_mcp  # noqa: E402

if __name__ == "__main__":
    build_mcp().run()  # stdio transport
