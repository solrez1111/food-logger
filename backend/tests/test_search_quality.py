"""Search-quality regression tests against realistic USDA SR-style names.

Pins the failures reported from real phone use (July 2026):
- "fried egg" must surface the actual fried egg first
- "egg" must rank whole eggs above egg-containing dishes (challah, noodles)
- "overeasy egg" (word not in USDA vocabulary) must still return eggs
- search-as-you-type prefixes ("scram", "greek yog") must match
- typo fallback ("yogrt") still works
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

CORPUS = [
    "Egg, whole, raw, fresh",
    "Egg, whole, cooked, fried",
    "Egg, whole, cooked, scrambled",
    "Egg, whole, cooked, hard-boiled",
    "Egg, whole, cooked, poached",
    "Egg, whole, cooked, omelet",
    "Egg, white, raw, fresh",
    "Egg substitute, liquid or frozen, fat free",
    "Eggplant, raw",
    "Eggnog",
    "Noodles, egg, cooked, enriched",
    "Fast foods, biscuit, with egg and sausage",
    "Bread, egg (challah)",
    "Chicken, broilers or fryers, breast, meat only, cooked, fried",
    "Potatoes, french fried, all types, salt added in processing, frozen, home-prepared, oven heated",
    "Fish, catfish, channel, cooked, breaded and fried",
    "Pork, cured, bacon, cooked, broiled, pan-fried or roasted",
    "Restaurant, Chinese, fried rice",
    "Cheese, cheddar",
    "Milk, reduced fat, fluid, 2% milkfat, with added vitamin A and vitamin D",
    "Yogurt, Greek, plain, nonfat, CORPUS",
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
                [(f"search-corpus-{i}", name) for i, name in enumerate(CORPUS)],
            )
        finally:
            await conn.close()

    async def cleanup():
        conn = await asyncpg.connect(TEST_URL)
        try:
            await conn.execute(
                "DELETE FROM food_log.foods WHERE source_id LIKE 'search-corpus-%'"
            )
        finally:
            await conn.close()

    asyncio.new_event_loop().run_until_complete(seed())
    with TestClient(app) as c:
        yield c
    asyncio.new_event_loop().run_until_complete(cleanup())


def names(client, q):
    res = client.get(f"/api/foods/search?q={q}", headers=H).json()
    return [r["name"] for r in res["results"]], res


def test_fried_egg_puts_the_fried_egg_first(client):
    got, res = names(client, "fried egg")
    assert got[0] == "Egg, whole, cooked, fried"
    assert res["matched"] == "fts"


def test_egg_ranks_whole_eggs_above_egg_dishes(client):
    got, _ = names(client, "egg")
    # top 5 are all actual eggs — not challah, noodles, eggnog, eggplant
    assert all(n.startswith("Egg,") for n in got[:5]), got[:5]
    assert "Eggplant, raw" not in got[:8]


def test_out_of_vocabulary_word_still_returns_eggs(client):
    # "overeasy" appears nowhere in USDA names; the egg half must survive
    got, res = names(client, "overeasy egg")
    assert res["matched"] == "fts_partial"
    assert res["offer_remote"] is True
    assert sum(1 for n in got[:6] if n.startswith("Egg")) >= 4, got[:6]


def test_prefix_matching_for_search_as_you_type(client):
    got, _ = names(client, "scram")
    assert got[0] == "Egg, whole, cooked, scrambled"
    got, _ = names(client, "greek yog")
    assert any("Yogurt, Greek" in n for n in got[:3]), got[:3]


def test_typo_still_falls_back_to_trigram(client):
    got, res = names(client, "yogrt")
    assert res["matched"] == "trgm"
    assert any("Yogurt" in n for n in got)


def test_multiword_confounders_do_not_hijack(client):
    got, _ = names(client, "fried rice")
    assert got[0] == "Restaurant, Chinese, fried rice"
    got, _ = names(client, "cheddar")
    assert got[0] == "Cheese, cheddar"
