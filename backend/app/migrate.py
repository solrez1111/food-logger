"""Apply migrations to the food_log schema. Run: python -m app.migrate

Migration 0001 is db/food_log_schema.pg.sql (the source of truth); subsequent
changes are numbered plain-SQL files in db/migrations/, applied in filename
order. Applied filenames are tracked in food_log.schema_migrations, and each
migration runs in its own transaction.

Deliberately NOT run on app startup (PLAN: explicit command only — the Neon
instance is shared with other schemas).
"""
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE = REPO_ROOT / "db" / "food_log_schema.pg.sql"
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"

BOOTSTRAP = """
CREATE SCHEMA IF NOT EXISTS food_log;
CREATE TABLE IF NOT EXISTS food_log.schema_migrations (
  filename   text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);
"""


def pending_migrations(applied: set[str]) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = [(SCHEMA_FILE.name, SCHEMA_FILE)]
    if MIGRATIONS_DIR.is_dir():
        files += [(p.name, p) for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    return [(name, path) for name, path in files if name not in applied]


async def migrate() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set")

    conn = await asyncpg.connect(url)
    try:
        await conn.execute(BOOTSTRAP)
        rows = await conn.fetch("SELECT filename FROM food_log.schema_migrations")
        applied = {r["filename"] for r in rows}

        todo = pending_migrations(applied)
        if not todo:
            print("migrations: up to date")
            return

        for name, path in todo:
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute(
                    "INSERT INTO food_log.schema_migrations (filename) VALUES ($1)", name
                )
            print(f"applied {name}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
