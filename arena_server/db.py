"""Postgres pool and boot-time schema."""

from pathlib import Path

from psycopg import AsyncConnection, sql
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

Pool = AsyncConnectionPool[AsyncConnection[DictRow]]

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def make_pool(database_url: str) -> Pool:
    return AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=8,
        open=False,
        connection_class=AsyncConnection[DictRow],
        kwargs={"row_factory": dict_row},
    )


async def apply_schema(pool: Pool) -> None:
    async with pool.connection() as conn:
        await conn.execute(sql.SQL(SCHEMA_PATH.read_text()))  # type: ignore[arg-type]
