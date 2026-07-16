"""Phase 5 — AI plate estimation (PLAN: photo-first, my primary logging mode).

Claude (vision-capable, Haiku-class by default) turns a plate photo and/or a
text description into food items with gram estimates. Each item is then
matched against the LOCAL catalog via app.search — the model names foods and
sizes them; it never invents nutrition data. Photos are processed, not stored.

Everything returns candidates only — nothing writes to log_entries until the
user confirms in the UI (confirm-before-save, always).
"""
from __future__ import annotations

import os

import asyncpg

from . import food_db
from .search import local_search

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

PLATE_TOOL = {
    "name": "report_plate",
    "description": "Report the foods identified on the plate with gram estimates.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "maxItems": 10,
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Generic food name in USDA style, most specific first, e.g. 'egg, fried' or 'rice, white, cooked' or 'chicken breast, grilled'. No brand names unless clearly visible.",
                        },
                        "grams": {"type": "number", "description": "Estimated grams as served (edible portion)."},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "reasoning": {"type": "string", "description": "Size cue used, e.g. 'palm-size piece ≈ 120 g'."},
                    },
                    "required": ["description", "grams", "confidence"],
                },
            },
            "note": {
                "type": "string",
                "description": "Only if something needs flagging: image unclear, not food, foods hidden under sauce, etc.",
            },
        },
        "required": ["items"],
    },
}

SYSTEM = """You estimate food quantities from plate photos and/or text descriptions for a personal nutrition log.

Identify each distinct food AS SERVED and estimate its weight in grams. Rules:
- Name foods generically in USDA style (food first, preparation after): 'egg, fried', 'rice, white, cooked', 'broccoli, steamed'. These names are matched against the USDA database.
- Estimate grams using visible size references: plate fraction, palm/deck-of-cards, cup volumes. State the cue in reasoning.
- Be conservative. Do not invent foods you cannot see or that are not described. Skip trivial garnishes (<5 g).
- Cooking fat: include it as its own item ONLY when clearly evident (visible oil sheen, fried items) — mark it low confidence.
- Mixed dishes you can't decompose (lasagna, stew): report the dish as one generic item.
- If the image is unclear or contains no food, return an empty items list and explain in note."""


async def call_claude_estimate(image_b64: str | None, text: str | None) -> dict:
    """One Claude call -> {'items': [...], 'note': str|None, 'model': str}.

    Isolated so tests can monkeypatch it (no network in CI) and the router
    stays thin. Raises RuntimeError when no API key is configured.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    import anthropic

    content = []
    if image_b64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
        })
    prompt = "Estimate this plate."
    if text:
        prompt = f"Estimate this plate. Additional context from the user: {text}" if image_b64 \
            else f"Estimate this meal from the description alone: {text}"
    content.append({"type": "text", "text": prompt})

    model = os.environ.get("ESTIMATE_MODEL", DEFAULT_MODEL)
    client = anthropic.AsyncAnthropic(timeout=45)
    msg = await client.messages.create(
        model=model,
        max_tokens=1500,
        system=SYSTEM,
        tools=[PLATE_TOOL],
        tool_choice={"type": "tool", "name": "report_plate"},
        messages=[{"role": "user", "content": content}],
    )
    block = next(b for b in msg.content if b.type == "tool_use")
    return {
        "items": block.input.get("items", []),
        "note": block.input.get("note"),
        "model": model,
    }


async def assemble_candidates(conn: asyncpg.Connection, items: list[dict]) -> list[dict]:
    """Match each estimated item against the local catalog.

    Selected match = top local search hit (with per-100g macros for live
    preview); up to 3 alternatives to swap to. No hit -> food None; the UI
    falls back to manual search for that row. Nutrition always comes from the
    catalog, never from the model.
    """
    out = []
    for item in items:
        desc = (item.get("description") or "").strip()
        grams = item.get("grams")
        if not desc or not isinstance(grams, (int, float)) or grams <= 0:
            continue
        rows, matched = await local_search(conn, desc, 4)
        macros = await food_db.macro_previews(conn, [r["id"] for r in rows])
        candidate = {
            "description": desc,
            "grams": round(float(grams), 1),
            "confidence": item.get("confidence", "low"),
            "reasoning": item.get("reasoning"),
            "match_quality": matched,
            "food": None,
            "alternatives": [],
        }
        if rows:
            top = rows[0]
            candidate["food"] = {
                "id": top["id"], "name": top["name"], "brand": top["brand"],
                "per_100g": macros.get(top["id"], {}),
            }
            candidate["alternatives"] = [
                {"id": r["id"], "name": r["name"], "brand": r["brand"],
                 "per_100g": macros.get(r["id"], {})}
                for r in rows[1:4]
            ]
        out.append(candidate)
    return out
