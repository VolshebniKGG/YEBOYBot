"""Reject incompatible databases before startup can change their schema or WAL mode."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from core_v2.db import CURRENT_SCHEMA_VERSION, Database


def sqlite_snapshot(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        objects = connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    return path.read_bytes(), objects, mode


@pytest.mark.parametrize("statements", [
    ["CREATE TABLE v2_schema(version INTEGER PRIMARY KEY)", "INSERT INTO v2_schema VALUES (999)"],
    ["CREATE TABLE v2_schema(revision INTEGER)", "INSERT INTO v2_schema VALUES (1)"],
    ["CREATE TABLE v2_schema(version INTEGER PRIMARY KEY)"],
    ["CREATE TABLE v2_schema(version TEXT)", "INSERT INTO v2_schema VALUES ('1')"],
    ["CREATE TABLE v2_schema(version INTEGER)", "INSERT INTO v2_schema VALUES (1),(1)"],
    ["CREATE TABLE v2_settings(guild_id INTEGER, value TEXT)", "INSERT INTO v2_settings VALUES(123,'preserve me')"],
    ["CREATE VIEW v2_schema AS SELECT 1 AS version"],
    ["CREATE TABLE v2_schema(version INTEGER PRIMARY KEY)", "INSERT INTO v2_schema VALUES (1)"],
])
async def test_rejected_sqlite_remains_byte_identical_and_no_tables_created(tmp_path, statements):
    path = (tmp_path / "База з пробілами.db").resolve()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
        connection.execute("CREATE TABLE user_data(value TEXT)")
        connection.execute("INSERT INTO user_data VALUES ('preserve unrelated data')")
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    before = sqlite_snapshot(path)
    database = Database("sqlite+aiosqlite:///" + str(path))
    try:
        with pytest.raises(RuntimeError, match="Unsupported schema"):
            await database.initialize()
    finally:
        await database.close()
    assert sqlite_snapshot(path) == before
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


async def test_sqlite_connect_cannot_change_future_schema_before_initialize(tmp_path):
    path = (tmp_path / "future.db").resolve()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE v2_schema(version INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO v2_schema VALUES(999)")
        connection.commit()
    before = sqlite_snapshot(path)
    database = Database("sqlite+aiosqlite:///" + str(path))
    try:
        with pytest.raises(RuntimeError, match="Unsupported schema"):
            await database.health()
    finally:
        await database.close()
    assert sqlite_snapshot(path) == before


async def test_fresh_bootstrap_and_repeated_start_preserve_existing_settings(tmp_path):
    path = (tmp_path / "supported.db").resolve()
    database = Database("sqlite+aiosqlite:///" + str(path))
    try:
        await database.initialize()
        await database.configure(123, levels=False, log_channel=456)
        await database.award(123, 789, now=1000)
        await database.initialize()
    finally:
        await database.close()
    restarted = Database("sqlite+aiosqlite:///" + str(path))
    try:
        await restarted.initialize()
        assert (await restarted.get_settings(123))["levels"] is False
        assert (await restarted.get_settings(123))["log_channel"] == 456
        assert (await restarted.rank(123, 789))["text_xp"] == 5
        async with restarted.engine.connect() as connection:
            assert (await connection.execute(text("SELECT version FROM v2_schema"))).scalars().all() == [CURRENT_SCHEMA_VERSION]
    finally:
        await restarted.close()


async def test_unrelated_tables_remain_untouched_on_initial_bootstrap(tmp_path):
    path = (tmp_path / "legacy.db").resolve()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE legacy_settings(id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO legacy_settings VALUES(1,'keep this')")
        connection.commit()
    database = Database("sqlite+aiosqlite:///" + str(path))
    try:
        await database.initialize()
        async with database.engine.connect() as connection:
            assert (await connection.execute(text("SELECT * FROM legacy_settings"))).all() == [(1, "keep this")]
    finally:
        await database.close()


async def test_initialize_rechecks_schema_on_already_open_pool(tmp_path):
    path = (tmp_path / "changed.db").resolve()
    database = Database("sqlite+aiosqlite:///" + str(path))
    try:
        await database.initialize()
        async with database.engine.begin() as connection:
            await connection.execute(text("UPDATE v2_schema SET version=999"))
        observed = []
        from sqlalchemy import event
        @event.listens_for(database.engine.sync_engine, "before_cursor_execute")
        def capture(_connection, _cursor, statement, *_):
            observed.append(statement)
        with pytest.raises(RuntimeError, match="Unsupported schema"):
            await database.initialize()
        assert not any(statement.lstrip().upper().startswith(("CREATE ", "ALTER ", "INSERT ", "UPDATE "))
                       for statement in observed)
    finally:
        await database.close()


@pytest.fixture
async def isolated_postgres_schema():
    url = os.getenv("TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("PostgreSQL integration uses TEST_DATABASE_URL")
    if make_url(url).database != "yeboy_test":
        raise RuntimeError("Schema guard tests require disposable database named yeboy_test")
    schema = "guard_test_" + uuid.uuid4().hex
    admin = create_async_engine(url)
    database = Database(url)
    # A dedicated search_path prevents reading or changing public/other test data.
    await database.close()
    database.engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        yield database
    finally:
        await database.close()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def test_postgres_fresh_schema_is_idempotent_and_preserves_settings(isolated_postgres_schema):
    database = isolated_postgres_schema
    await database.initialize()
    await database.configure(123, levels=False)
    await database.initialize()
    assert (await database.get_settings(123))["levels"] is False


@pytest.mark.parametrize("version", [999, None])
async def test_postgres_invalid_marker_creates_no_other_tables(isolated_postgres_schema, version):
    database = isolated_postgres_schema
    async with database.engine.begin() as connection:
        await connection.execute(text("CREATE TABLE v2_schema(version INTEGER PRIMARY KEY)"))
        if version is not None:
            await connection.execute(text("INSERT INTO v2_schema VALUES (:version)"), {"version": version})
    with pytest.raises(RuntimeError, match="Unsupported schema"):
        await database.initialize()
    async with database.engine.connect() as connection:
        tables = (await connection.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()"))).scalars().all()
        assert tables == ["v2_schema"]
        assert (await connection.execute(text("SELECT version FROM v2_schema"))).scalars().all() == ([] if version is None else [999])


async def test_postgres_materialized_marker_is_not_fresh_database(isolated_postgres_schema):
    database = isolated_postgres_schema
    async with database.engine.begin() as connection:
        await connection.execute(text("CREATE MATERIALIZED VIEW v2_schema AS SELECT 999 AS version"))
    with pytest.raises(RuntimeError, match="Unsupported schema"):
        await database.initialize()
    async with database.engine.connect() as connection:
        objects = (await connection.execute(text(
            "SELECT relname, relkind::text FROM pg_class WHERE relnamespace=current_schema()::regnamespace"))).all()
        assert objects == [("v2_schema", "m")]


async def test_sqlite_initial_bootstrap_failure_rolls_back_all_tables(tmp_path):
    from sqlalchemy import event
    path = (tmp_path / "failed-bootstrap.db").resolve()
    database = Database("sqlite+aiosqlite:///" + str(path))
    creates = []
    @event.listens_for(database.engine.sync_engine, "before_cursor_execute")
    def fail_second_create(_connection, _cursor, statement, *_):
        if statement.lstrip().upper().startswith("CREATE TABLE"):
            creates.append(statement)
            if len(creates) == 2:
                raise RuntimeError("simulated bootstrap failure")
    try:
        with pytest.raises(RuntimeError, match="simulated bootstrap"):
            await database.initialize()
    finally:
        await database.close()
    assert len(creates) == 2
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    retry = Database("sqlite+aiosqlite:///" + str(path))
    try:
        await retry.initialize()
        assert await retry.health()
    finally:
        await retry.close()


async def test_sqlite_two_engines_bootstrap_once(tmp_path):
    import asyncio
    path = (tmp_path / "concurrent-bootstrap.db").resolve()
    url = "sqlite+aiosqlite:///" + str(path)
    first, second = Database(url), Database(url)
    try:
        await asyncio.gather(first.initialize(), second.initialize())
        await first.configure(123, levels=False)
        await second.initialize()
        assert (await second.get_settings(123))["levels"] is False
    finally:
        await first.close()
        await second.close()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT version FROM v2_schema").fetchall() == [(CURRENT_SCHEMA_VERSION,)]


async def test_postgres_legacy_v1_requires_explicit_migration(isolated_postgres_schema):
    from test_migrations_core import legacy_metadata
    from core_v2.db import MigrationRequired
    database = isolated_postgres_schema
    async with database.engine.begin() as connection:
        await connection.run_sync(lambda sync: legacy_metadata().create_all(sync))
        await connection.execute(text("INSERT INTO v2_schema VALUES (1)"))
        await connection.execute(text("INSERT INTO v2_settings (guild_id,levels,automod) VALUES (11,false,false)"))
    with pytest.raises(MigrationRequired):
        await database.initialize()
    async with database.engine.connect() as connection:
        assert (await connection.execute(text("SELECT version FROM v2_schema"))).scalars().all() == [1]
        assert (await connection.execute(text("SELECT levels FROM v2_settings WHERE guild_id=11"))).scalar_one() is False
        columns = (await connection.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() "
            "AND table_name='v2_cases'"))).scalars().all()
        assert "updated" not in columns
