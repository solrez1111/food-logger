"""Integration tests for catalog upserts — need a real Postgres with the
food_log schema applied. Skipped unless TEST_DATABASE_URL is set:

    TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_food_db.py
"""
import asyncio
import os

import pytest

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL not set")

ITEM = {
    "food": {
        "source": "off", "source_id": "test-upsert-1", "name": "Test Yogurt",
        "brand": "TestCo", "barcode": "0099999999990",
    },
    "portions": [{"description": "1 container", "gram_weight": 150}],
    "nutrients": {"kcal": 59, "protein_g": 10.2, "sodium_mg": 36},
    "raw": {"anything": True},
}


@pytest.fixture()
def conn():
    import asyncpg

    loop = asyncio.new_event_loop()
    c = loop.run_until_complete(asyncpg.connect(TEST_URL))
    yield loop, c
    loop.run_until_complete(c.execute(
        "DELETE FROM food_log.foods WHERE source = 'off' AND source_id LIKE 'test-upsert-%'"
    ))
    loop.run_until_complete(c.close())
    loop.close()


def test_upsert_is_idempotent_and_replaces_children(conn):
    loop, c = conn
    from app.food_db import food_detail, upsert_food

    id1 = loop.run_until_complete(upsert_food(c, ITEM))
    # re-import with changed values must update in place, same id, no dup children
    changed = {**ITEM, "nutrients": {"kcal": 61}, "portions": []}
    id2 = loop.run_until_complete(upsert_food(c, changed))
    assert id1 == id2

    detail = loop.run_until_complete(food_detail(c, id1))
    assert detail["nutrients"] == {"kcal": 61.0}
    assert detail["portions"] == []


def test_find_by_barcode_matches_scanned_upc_variant(conn):
    loop, c = conn
    from app.food_db import find_by_barcode, upsert_food

    loop.run_until_complete(upsert_food(c, ITEM))
    # stored as 13-digit 0099999999990; a 12-digit UPC-A scan must still hit
    hit = loop.run_until_complete(find_by_barcode(c, "099999999990"))
    assert hit is not None and hit["source_id"] == "test-upsert-1"
    miss = loop.run_until_complete(find_by_barcode(c, "111111111111"))
    assert miss is None
