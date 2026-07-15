"""Phase 2 API integration tests — real Postgres, real HTTP layer.

Skipped unless TEST_DATABASE_URL is set (schema must be applied). Remote legs
(OFF/FDC network) are exercised in production, not here.
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

from app.main import app  # noqa: E402

H = {"Authorization": "Bearer test-token"}

CUSTOM = {
    "name": "ZZTest Greek Yogurt Plain",
    "brand": "TestCo",
    "barcode": "0088888888880",
    "portions": [{"description": "1 container", "gram_weight": 150}],
    "nutrients": {"kcal": 59, "protein_g": 10.2, "sodium_mg": 36},
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:   # context manager runs the lifespan (db pool)
        yield c
    # cleanup: drop everything this module created
    import asyncpg

    async def cleanup():
        conn = await asyncpg.connect(TEST_URL)
        try:
            await conn.execute(
                "DELETE FROM food_log.foods WHERE source = 'custom' AND name LIKE 'ZZTest%'"
            )
        finally:
            await conn.close()

    asyncio.new_event_loop().run_until_complete(cleanup())


def test_search_requires_auth(client):
    assert client.get("/api/foods/search?q=yogurt").status_code == 401


def test_create_custom_food_roundtrip(client):
    res = client.post("/api/foods", json=CUSTOM, headers=H)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["food"]["source"] == "custom"
    assert body["nutrients"] == {"kcal": 59.0, "protein_g": 10.2, "sodium_mg": 36.0}
    assert body["portions"][0]["description"] == "1 container"

    detail = client.get(f"/api/foods/{body['food']['id']}", headers=H).json()
    assert detail["food"]["name"] == "ZZTest Greek Yogurt Plain"


def test_create_rejects_bad_input(client):
    assert client.post("/api/foods", json={**CUSTOM, "nutrients": {}}, headers=H).status_code == 422
    assert client.post("/api/foods", json={**CUSTOM, "nutrients": {"Kcal!": 5}}, headers=H).status_code == 422
    assert client.post("/api/foods", json={**CUSTOM, "nutrients": {"kcal": -5}}, headers=H).status_code == 422
    bad_portion = {**CUSTOM, "portions": [{"description": "x", "gram_weight": 0}]}
    assert client.post("/api/foods", json=bad_portion, headers=H).status_code == 422


def test_fts_search_finds_exact_words(client):
    client.post("/api/foods", json=CUSTOM, headers=H)
    body = client.get("/api/foods/search?q=zztest greek yogurt", headers=H).json()
    assert body["matched"] == "fts"
    names = [r["name"] for r in body["results"]]
    assert "ZZTest Greek Yogurt Plain" in names
    hit = next(r for r in body["results"] if r["name"] == "ZZTest Greek Yogurt Plain")
    assert hit["per_100g"]["kcal"] == 59.0  # macro preview rides along


def test_typo_falls_back_to_trigram(client):
    client.post("/api/foods", json=CUSTOM, headers=H)
    body = client.get("/api/foods/search?q=yogrt", headers=H).json()
    assert body["matched"] == "trgm"
    assert any("Yogurt" in r["name"] for r in body["results"])


def test_no_match_offers_remote(client):
    body = client.get("/api/foods/search?q=qqqxyzzz", headers=H).json()
    assert body["matched"] == "none"
    assert body["results"] == []
    assert body["offer_remote"] is True


def test_barcode_hits_local_before_network(client):
    client.post("/api/foods", json=CUSTOM, headers=H)
    # stored as 13-digit; scanned as 12-digit UPC-A — variant match, no network
    res = client.get("/api/foods/barcode/088888888880", headers=H)
    assert res.status_code == 200
    assert res.json()["food"]["name"] == "ZZTest Greek Yogurt Plain"


def test_get_food_404(client):
    assert client.get("/api/foods/99999999", headers=H).status_code == 404
