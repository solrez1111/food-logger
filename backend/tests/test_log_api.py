"""Phase 3 integration tests — logging, rollups+coverage, weights, targets,
favorites. Real Postgres via TEST_DATABASE_URL; math pinned with known values.

All test data lives in 1999 dates and ZZTest-prefixed foods, cleaned up on
module teardown.
"""
import asyncio
import os
import uuid

import pytest

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL not set")

os.environ["API_TOKEN"] = "test-token"
if TEST_URL:
    os.environ["DATABASE_URL"] = TEST_URL

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

H = {"Authorization": "Bearer test-token"}
DAY = "1999-01-02"

FOOD_A = {  # reports sodium + potassium
    "name": "ZZTest Rollup A",
    "portions": [{"description": "1 cup", "gram_weight": 245}],
    "nutrients": {"kcal": 100, "protein_g": 10, "sodium_mg": 500, "potassium_mg": 300},
}
FOOD_B = {  # does NOT report sodium/potassium — drives coverage < 1
    "name": "ZZTest Rollup B",
    "nutrients": {"kcal": 50, "protein_g": 5},
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    import asyncpg

    async def cleanup():
        conn = await asyncpg.connect(TEST_URL)
        try:
            await conn.execute(
                "DELETE FROM food_log.log_entries WHERE user_id = 1 AND date BETWEEN '1999-01-01' AND '1999-12-31'"
            )
            await conn.execute(
                "DELETE FROM food_log.foods WHERE source = 'custom' AND name LIKE 'ZZTest Rollup%'"
            )
            await conn.execute(
                "DELETE FROM food_log.body_weight WHERE user_id = 1 AND date BETWEEN '1999-01-01' AND '1999-12-31'"
            )
            await conn.execute(
                "DELETE FROM food_log.targets WHERE user_id = 1 AND (effective_date < '2000-01-01' OR effective_date >= '3000-01-01')"
            )
        finally:
            await conn.close()

    asyncio.new_event_loop().run_until_complete(cleanup())


@pytest.fixture(scope="module")
def foods(client):
    a = client.post("/api/foods", json=FOOD_A, headers=H).json()
    b = client.post("/api/foods", json=FOOD_B, headers=H).json()
    return {"a": a["food"]["id"], "b": b["food"]["id"],
            "a_portion": a["portions"][0]["id"]}


def log(client, **kw):
    return client.post("/api/log", json={"date": DAY, **kw}, headers=H)


# --- logging -----------------------------------------------------------------

def test_log_requires_auth(client):
    assert client.post("/api/log", json={"date": DAY, "food_id": 1, "grams": 100}).status_code == 401


def test_log_with_grams_and_per_entry_macros(client, foods):
    res = log(client, food_id=foods["a"], grams=200)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["grams"] == 200
    assert body["per_entry"] == {"kcal": 200.0, "protein_g": 20.0, "sodium_mg": 1000.0}
    assert body["entry_method"] == "manual"


def test_log_with_portion_converts_to_grams(client, foods):
    res = log(client, food_id=foods["a"], portion_id=foods["a_portion"], portion_qty=0.5)
    assert res.status_code == 201
    body = res.json()
    assert body["grams"] == 122.5  # 245 * 0.5
    assert body["portion"]["description"] == "1 cup"
    # remove it again to keep the rollup numbers clean
    assert client.delete(f"/api/log/{body['id']}", headers=H).status_code == 204


def test_replay_same_client_id_does_not_duplicate(client, foods):
    cid = str(uuid.uuid4())
    first = log(client, food_id=foods["b"], grams=100, client_id=cid)
    assert first.status_code == 201
    replay = log(client, food_id=foods["b"], grams=100, client_id=cid)
    assert replay.json()["id"] == first.json()["id"]

    day = client.get(f"/api/log/{DAY}", headers=H).json()
    same = [e for e in day["entries"] if e["client_id"] == cid]
    assert len(same) == 1


def test_log_validation_rejects_bad_shapes(client, foods):
    assert log(client, food_id=foods["a"]).status_code == 422                       # no amount
    assert log(client, food_id=foods["a"], grams=100, portion_id=1, portion_qty=1).status_code == 422
    assert log(client, food_id=999999999, grams=100).status_code == 404             # no such food
    wrong_portion = log(client, food_id=foods["b"], portion_id=foods["a_portion"], portion_qty=1)
    assert wrong_portion.status_code == 422                                          # portion of other food


# --- rollups + coverage (the math the plan pins) ------------------------------

def test_day_summary_totals_and_coverage(client, foods):
    # state from tests above: A 200g + B 100g on DAY
    s = client.get(f"/api/summary/{DAY}", headers=H).json()
    assert s["total_grams"] == 300.0
    assert s["n_entries"] == 2
    n = s["nutrients"]
    assert n["kcal"] == {"total": 250.0, "coverage": 1.0}          # 200 + 50
    assert n["protein_g"] == {"total": 25.0, "coverage": 1.0}      # 20 + 5
    # only A (200 of 300 g) reports sodium: total 1000, coverage 2/3
    assert n["sodium_mg"]["total"] == 1000.0
    assert n["sodium_mg"]["coverage"] == pytest.approx(0.667, abs=0.001)
    # nobody reports fiber: honest null + zero coverage, not a fake zero
    assert n["fiber_g"] == {"total": None, "coverage": 0.0}


def test_nutrient_summary_any_key(client, foods):
    s = client.get(f"/api/summary/nutrient/potassium_mg?start={DAY}&end={DAY}", headers=H).json()
    assert s["days"] == [{
        "date": DAY, "total": 600.0,                                # 300/100g * 200g
        "coverage": pytest.approx(0.667, abs=0.001), "n_entries": 2,
    }]
    assert client.get(f"/api/summary/nutrient/DROP TABLE?start={DAY}&end={DAY}", headers=H).status_code == 422


def test_range_summary(client, foods):
    s = client.get(f"/api/summary?start=1999-01-01&end=1999-01-03", headers=H).json()
    assert len(s["days"]) == 1  # only DAY has entries; empty days aren't invented
    assert s["days"][0]["date"] == DAY


# --- targets + remaining -------------------------------------------------------

def test_targets_versioning_and_remaining(client, foods):
    r = client.put("/api/targets", json={"effective_date": "1999-01-01", "kcal": 2000, "sodium_mg": 1500}, headers=H)
    assert r.status_code == 201
    client.put("/api/targets", json={"effective_date": "1999-01-03", "kcal": 1800, "sodium_mg": 1300}, headers=H)

    s = client.get(f"/api/summary/{DAY}", headers=H).json()        # Jan 2 -> Jan 1 target rules
    assert s["target"]["kcal"] == 2000
    assert s["remaining"]["kcal"] == 1750.0                        # 2000 - 250
    assert s["remaining"]["sodium_mg"] == 500.0                    # 1500 - 1000

    t = client.get("/api/targets?on=1999-01-04", headers=H).json() # Jan 4 -> Jan 3 target rules
    assert t["current"]["kcal"] == 1800

    t = client.get("/api/targets?on=1998-12-31", headers=H).json() # before any target
    assert t["current"] is None

    assert client.put("/api/targets", json={"effective_date": "1999-01-05"}, headers=H).status_code == 422


# --- weight --------------------------------------------------------------------

def test_weight_upsert_and_range(client):
    assert client.post("/api/weight", json={"date": DAY, "weight_lb": 180.5}, headers=H).status_code == 201
    client.post("/api/weight", json={"date": DAY, "weight_lb": 179.0}, headers=H)  # same day -> update
    w = client.get("/api/weight?start=1999-01-01&end=1999-01-31", headers=H).json()
    assert len(w["weights"]) == 1
    assert w["weights"][0]["weight_lb"] == 179.0


# --- favorites + patch/delete ----------------------------------------------------

def test_favorite_one_tap_log(client, foods):
    fav = client.post("/api/favorites",
                      json={"food_id": foods["b"], "default_grams": 100, "label": "test staple"},
                      headers=H)
    assert fav.status_code == 201
    fav_id = fav.json()["id"]

    lst = client.get("/api/favorites", headers=H).json()["favorites"]
    mine = next(f for f in lst if f["id"] == fav_id)
    assert mine["name"] == "test staple"
    assert mine["per_serving"]["kcal"] == 50.0

    entry = log(client, favorite_id=fav_id)
    assert entry.status_code == 201
    assert entry.json()["entry_method"] == "favorite"
    assert entry.json()["grams"] == 100

    assert client.delete(f"/api/log/{entry.json()['id']}", headers=H).status_code == 204
    assert client.delete(f"/api/favorites/{fav_id}", headers=H).status_code == 204
    both = client.post("/api/favorites",
                       json={"food_id": foods["b"], "default_grams": 100,
                             "portion_id": 1, "portion_qty": 1},
                       headers=H)
    assert both.status_code == 422


def test_patch_and_delete_entry(client, foods):
    entry = log(client, food_id=foods["b"], grams=50).json()
    patched = client.patch(f"/api/log/{entry['id']}", json={"grams": 80}, headers=H).json()
    assert patched["grams"] == 80
    assert patched["per_entry"]["kcal"] == 40.0

    assert client.patch("/api/log/999999999", json={"grams": 1}, headers=H).status_code == 404
    assert client.delete(f"/api/log/{entry['id']}", headers=H).status_code == 204
    assert client.delete(f"/api/log/{entry['id']}", headers=H).status_code == 404
