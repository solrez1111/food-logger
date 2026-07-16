"""Local food search: FTS with prefix terms + OR fallback, similarity- and
first-word-weighted ranking, trigram typo fallback. Shared by the /api/foods
router and the Phase 5 plate estimator. Tuned against
tests/test_search_quality.py.
"""
from __future__ import annotations

import re

import asyncpg

# Search strategy (tuned against a realistic USDA-name corpus — see
# tests/test_search_quality.py):
#   1. Match ANY query word (OR, with prefix on every term so search-as-you-
#      type works: 'scram' finds scrambled). Pure AND returned nothing for
#      "overeasy egg" — one out-of-vocabulary word killed the whole query.
#   2. Rank rows matching ALL words first, then by word_similarity(query,
#      name) — plain ts_rank ties on almost every row, and the old
#      shortest-name tiebreak put "Bread, egg (challah)" above actual eggs.
#   3. Trigram fallback only when no word matches at all (typos: 'yogrt').
SEARCH_FTS = """
SELECT f.id, f.source, f.name, f.brand, f.barcode,
       (f.search_vec @@ to_tsquery('english', $2))::int AS full_match,
       -- USDA names lead with the food itself ("Egg, whole, ..."), so a name
       -- whose FIRST word is one of the query words is almost always the
       -- thing being searched for, not a dish containing it ("Bread, egg").
       ((lower(substring(f.name from '^[A-Za-z0-9]+')) = ANY($5))::int * 3
        + word_similarity($1, f.name) * 2
        + ts_rank(f.search_vec, to_tsquery('english', $3)))::float AS score
FROM food_log.foods f
WHERE f.search_vec @@ to_tsquery('english', $3)
ORDER BY full_match DESC, score DESC, length(f.name) ASC
LIMIT $4
"""

SEARCH_TRGM = """
SELECT f.id, f.source, f.name, f.brand, f.barcode,
       1 AS full_match,
       word_similarity($1, f.name)::float AS score
FROM food_log.foods f
WHERE word_similarity($1, f.name) > 0.4
ORDER BY score DESC, length(f.name) ASC
LIMIT $2
"""

_WORD_RE = re.compile(r"[a-z0-9]+")


def _ts_queries(q: str) -> tuple[str, str, list[str]] | None:
    """(AND-query, OR-query, raw terms) with prefix matching on every term, or
    None if the input has no usable words. Tokens are [a-z0-9]+ only — safe to
    splice into to_tsquery."""
    terms = _WORD_RE.findall(q.lower())[:8]
    if not terms:
        return None
    return (
        " & ".join(f"{t}:*" for t in terms),
        " | ".join(f"{t}:*" for t in terms),
        terms,
    )


async def local_search(conn: asyncpg.Connection, q: str, limit: int):
    queries = _ts_queries(q)
    rows = []
    if queries:
        and_q, or_q, terms = queries
        rows = await conn.fetch(SEARCH_FTS, q, and_q, or_q, limit, terms)
    if rows:
        matched = "fts" if rows[0]["full_match"] else "fts_partial"
        return rows, matched
    rows = await conn.fetch(SEARCH_TRGM, q, limit)
    return rows, ("trgm" if rows else "none")


