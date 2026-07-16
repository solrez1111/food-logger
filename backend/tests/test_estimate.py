"""Phase 5 tests: candidate assembly against the real catalog + the endpoint
with the Claude call mocked (its real behavior is exercised in prod).
"""
import asyncio
import os

import pytest

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL not set")

os.environ["API_TOKEN"] = "test-token"
if TEST_URL:
    os.environ["DATABASE_URL"] = TEST_URL

from fastapi.testclient import TestClient  # noqa: E402

from app import estimate as est  # noqa: E402
from app.main import app  # noqa: E402

H = {"Authorization": "Bearer test-token"}

CORPUS = [
    ("est-corpus-1", "Egg, whole, cooked, fried"),
    ("est-corpus-2", "Rice, white, long-grain, regular, enriched, cooked"),
    ("est-corpus-3", "Broccoli, cooked, boiled, drained, without salt"),
]

CLAUDE_ITEMS = [
    {"description": "egg, fried", "grams": 55, "confidence": "high",
     "reasoning": "one standard fried egg ≈ 55 g"},
    {"description": "rice, white, cooked", "grams": 160, "confidence": "medium",
     "reasoning": "about a cup"},
    {"description": "unicorn steak", "grams": 100, "confidence": "low",
     "reasoning": "no such food — must come back unmatched"},
    {"description": "", "grams": 50, "confidence": "high"},          # junk: dropped
    {"description": "broccoli, steamed", "grams": -5, "confidence": "high"},  # junk: dropped
]


@pytest.fixture(scope="module")
def client():
    import asyncpg

    async def seed():
        conn = await asyncpg.connect(TEST_URL)
        try:
            await conn.executemany(
                """INSERT INTO food_log.foods (source, source_id, name)
                   VALUES ('fdc_sr_legacy', $1, $2)
                   ON CONFLICT (source, source_id) DO UPDATE SET name = EXCLUDED.name""",
                CORPUS,
            )
            ids = {r["source_id"]: r["id"] for r in await conn.fetch(
                "SELECT id, source_id FROM food_log.foods WHERE source_id LIKE 'est-corpus-%'")}
            await conn.executemany(
                """INSERT INTO food_log.nutrients (food_id, nutrient_key, amount_per_100g)
                   VALUES ($1, $2, $3) ON CONFLICT (food_id, nutrient_key)
                   DO UPDATE SET amount_per_100g = EXCLUDED.amount_per_100g""",
                [(ids["est-corpus-1"], "kcal", 196), (ids["est-corpus-1"], "sodium_mg", 207),
                 (ids["est-corpus-2"], "kcal", 130)],
            )
        finally:
            await conn.close()

    async def cleanup():
        conn = await asyncpg.connect(TEST_URL)
        try:
            await conn.execute("DELETE FROM food_log.foods WHERE source_id LIKE 'est-corpus-%'")
        finally:
            await conn.close()

    asyncio.new_event_loop().run_until_complete(seed())
    with TestClient(app) as c:
        yield c
    asyncio.new_event_loop().run_until_complete(cleanup())


@pytest.fixture()
def mock_claude(monkeypatch):
    async def fake(image_b64, text):
        return {"items": CLAUDE_ITEMS, "note": None, "model": "mock"}
    monkeypatch.setattr(est, "call_claude_estimate", fake)


def test_estimate_requires_auth(client):
    assert client.post("/api/log/estimate", json={"text": "eggs"}).status_code == 401


def test_estimate_validates_input(client):
    assert client.post("/api/log/estimate", json={}, headers=H).status_code == 422
    bad = client.post("/api/log/estimate", json={"image_b64": "!!!not-base64!!!", "text": "x"}, headers=H)
    assert bad.status_code == 422


def test_estimate_returns_matched_candidates(client, mock_claude):
    res = client.post("/api/log/estimate", json={"text": "fried egg and a cup of rice"}, headers=H)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["entry_method"] == "ai_text"

    by_desc = {c["description"]: c for c in body["candidates"]}
    # junk rows (empty description, negative grams) never surface
    assert set(by_desc) == {"egg, fried", "rice, white, cooked", "unicorn steak"}

    egg = by_desc["egg, fried"]
    assert egg["food"]["name"] == "Egg, whole, cooked, fried"
    assert egg["grams"] == 55
    assert egg["food"]["per_100g"]["kcal"] == 196     # nutrition from catalog, not model
    assert egg["confidence"] == "high"

    rice = by_desc["rice, white, cooked"]
    assert "Rice, white" in rice["food"]["name"]

    # unmatched item degrades honestly: no food, UI falls back to manual search
    unicorn = by_desc["unicorn steak"]
    assert unicorn["food"] is None or "steak" not in (unicorn["food"]["name"].lower())


def test_estimate_photo_flag(client, mock_claude):
    import base64
    fake_jpeg = base64.b64encode(b"\xff\xd8\xff fake jpeg bytes").decode()
    res = client.post("/api/log/estimate", json={"image_b64": fake_jpeg}, headers=H)
    assert res.status_code == 200
    assert res.json()["entry_method"] == "ai_photo"


def test_estimate_503_without_api_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = client.post("/api/log/estimate", json={"text": "eggs"}, headers=H)
    assert res.status_code == 503
