"""Shared fixtures.

Tests that need a real Postgres take the `postgres_url` fixture. It reads
TEST_STATE_DB_URL (for example the database docker-compose.yml starts) and
empties the store's tables before each test, so point it only at a database
you can throw away. Without it those tests are skipped, unless
REQUIRE_POSTGRES_TESTS=1 (set in CI), which makes a missing database a
failure instead.
"""

import os

import psycopg
import pytest

from ospsd_team4_product.store import PostgresStateStore

TEST_DB_VARIABLE = "TEST_STATE_DB_URL"
REQUIRE_VARIABLE = "REQUIRE_POSTGRES_TESTS"
TABLES = ("progress_log", "remaining_work", "plan_blocks", "approved_plans")


def _test_db_url() -> str:
    url = os.environ.get(TEST_DB_VARIABLE)
    if url:
        return url
    if os.environ.get(REQUIRE_VARIABLE) == "1":
        pytest.fail(f"{REQUIRE_VARIABLE}=1 but {TEST_DB_VARIABLE} is not set")
    pytest.skip(f"set {TEST_DB_VARIABLE} to run the Postgres tests")


@pytest.fixture
def postgres_url() -> str:
    """A Postgres URL whose store tables exist and are empty."""
    url = _test_db_url()
    PostgresStateStore.connect(url, min_size=1, max_size=1).close()
    with psycopg.connect(url) as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)}")
    return url


@pytest.fixture
def empty_postgres_url() -> str:
    """A Postgres URL with none of the store's tables."""
    url = _test_db_url()
    with psycopg.connect(url) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {', '.join(TABLES)}")
    return url
