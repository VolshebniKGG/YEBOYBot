"""Transactional PostgreSQL upgrades, locks and preservation; no Discord login."""
from contextlib import asynccontextmanager
import os
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from core_v2 import postgres_migrations as pg
from core_v2.db import required_columns
from core_v2.migrations import MigrationError
from test_migrations_core import legacy_metadata


class FakeConnection:
    def __init__(self, *, backend="postgresql", transaction=True, schema="public", advisory=True):
        self.dialect = SimpleNamespace(name=backend)
        self.transaction = SimpleNamespace(sync_transaction=object()) if transaction else None
        self.schema = schema
        self.advisory = advisory
        self.info = {}
        self.calls = []

    def get_transaction(self):
        return self.transaction

    def in_transaction(self):
        return self.transaction is not None

    async def scalar(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        if "current_schema" in str(statement):
            return self.schema
        if "pg_try_advisory" in str(statement):
            return self.advisory
        raise AssertionError("Unexpected SQL in the preflight fixture")

    async def run_sync(self, function):
        self.calls.append(("inspect", None))
        return 1

    async def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        return SimpleNamespace(rowcount=1)


@pytest.mark.parametrize("backend,transaction", [("sqlite", True), ("postgresql", False)])
async def test_source_lock_refuses_wrong_backend_or_missing_transaction_before_sql(backend, transaction):
    connection = FakeConnection(backend=backend, transaction=transaction)
    with pytest.raises(MigrationError):
        await pg.lock_source(connection)
    assert connection.calls == []


async def test_inspection_refuses_non_public_schema_without_adopting_tables():
    connection = FakeConnection(schema="foreign_schema")
    with pytest.raises(MigrationError, match="public"):
        await pg.inspect_postgres(connection)
    assert connection.calls == [("SELECT current_schema()", None)]


async def test_busy_maintenance_refuses_before_metadata_or_table_locks():
    connection = FakeConnection(advisory=False)
    with pytest.raises(MigrationError, match="Another"):
        await pg.lock_source(connection)
    assert len(connection.calls) == 1
    assert connection.info == {}


async def test_missing_source_lock_proof_refuses_before_any_sql():
    connection = FakeConnection()
    with pytest.raises(MigrationError, match="source locks"):
        await pg.migrate_postgres(connection)
    assert connection.calls == []


async def test_lock_proof_from_completed_transaction_cannot_authorize_later_ddl():
    connection = FakeConnection()
    await pg.lock_source(connection)
    connection.calls.clear()
    connection.transaction = SimpleNamespace(sync_transaction=object())
    with pytest.raises(MigrationError, match="current transaction"):
        await pg.migrate_postgres(connection)
    assert connection.calls == []


@pytest.fixture
async def fresh_database():
    """Factory of NEW disposable DBs; never cleans an existing configured DB.

    Only TEST_POSTGRES_MAINTENANCE_URL whose admin DB starts yeboy_test is used.
    The caller needs CREATE DATABASE in the disposable test server. Cleanup
    addresses only names generated and successfully created by this fixture.
    """
    configured = os.getenv("TEST_POSTGRES_MAINTENANCE_URL")
    if not configured:
        pytest.skip("Disposable PostgreSQL maintenance server not configured")
    base = make_url(configured)
    if base.drivername != "postgresql+asyncpg" or not (base.database or "").startswith("yeboy_test"):
        pytest.skip("Maintenance tests require a dedicated yeboy_test admin database")
    admin = create_async_engine(base, isolation_level="AUTOCOMMIT")

    @asynccontextmanager
    async def allocate():
        name = "yeboy_test_engine_" + uuid.uuid4().hex
        created = False
        try:
            async with admin.connect() as connection:
                await connection.execute(text(f'CREATE DATABASE "{name}"'))
                created = True
            yield base.set(database=name).render_as_string(hide_password=False)
        finally:
            if created:
                async with admin.connect() as connection:
                    # This is a newly created test DB, never the configured admin DB.
                    await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    try:
        yield allocate
    finally:
        await admin.dispose()


@pytest.fixture
async def maintenance_database(fresh_database):
    async with fresh_database() as url:
        yield url


def maintenance_engine(url):
    return create_async_engine(url, connect_args={"server_settings": {
        "search_path": "public", "lock_timeout": "3000", "statement_timeout": "10000"}})


async def seed_v1(url):
    """Seed settings, XP, cases/jobs in two guilds and a custom preserved index."""
    engine = maintenance_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: legacy_metadata().create_all(sync))
            await connection.execute(text("INSERT INTO v2_schema VALUES (1)"))
            for guild in (11, 22):
                await connection.execute(text("INSERT INTO v2_settings VALUES (:guild,false,false,10,20,30)"), {"guild": guild})
                await connection.execute(text("INSERT INTO v2_xp VALUES (:guild,101,15,5,100,200)"), {"guild": guild})
                await connection.execute(text("INSERT INTO v2_cases VALUES (:id,:guild,101,202,'warn','Зберегти',1234.5,'pending')"),
                                         {"id": str(guild), "guild": guild})
                await connection.execute(text("INSERT INTO v2_jobs VALUES (:id,:guild,101,303,'нагадування',1000,0,0,'pending')"),
                                         {"id": str(guild), "guild": guild})
                await connection.execute(text("INSERT INTO v2_role_panels VALUES (:id,:guild,404)"),
                                         {"id": str(guild), "guild": guild})
            await connection.execute(text("CREATE INDEX preserve_case_index ON v2_cases(guild_id,created)"))
    finally:
        await engine.dispose()


async def snapshot(connection):
    result = {}
    for name, columns in required_columns(1).items():
        fields = ",".join('"' + column + '"' for column in sorted(columns))
        ordering = ",".join(str(index) for index in range(1, len(columns) + 1))
        result[name] = (await connection.execute(text(f'SELECT {fields} FROM public."{name}" ORDER BY {ordering}'))).all()
    return result


async def test_real_upgrade_preserves_all_payload_indexes_and_timestamp(maintenance_database):
    await seed_v1(maintenance_database)
    engine = maintenance_engine(maintenance_database)
    try:
        async with engine.begin() as connection:
            before = await snapshot(connection)
            # SERIAL PK defaults and their owned sequences are native/restorable.
            await pg.validate_defaults(connection)
            assert await pg.lock_source(connection) == 1
            assert await pg.migrate_postgres(connection) == ["002_case_updated_at"]
            after = await snapshot(connection)
            assert before | {"v2_schema": after["v2_schema"]} == after
            assert after["v2_schema"] == [(2,)]
            assert (await connection.execute(text("SELECT created,updated FROM v2_cases ORDER BY id"))).all() == [(1234.5, 1234.5)] * 2
            assert await connection.scalar(text("SELECT count(*) FROM pg_indexes WHERE indexname='preserve_case_index'")) == 1
        async with engine.begin() as connection:
            # The new constant timestamp default is admitted as well.
            await pg.validate_defaults(connection)
            assert await pg.lock_source(connection) == 2
            assert await pg.migrate_postgres(connection) == []
    finally:
        await engine.dispose()


@pytest.mark.parametrize("statements,diagnostic", [
    (["CREATE SEQUENCE custom_seq",
      "ALTER TABLE v2_settings ALTER COLUMN log_channel SET DEFAULT nextval('custom_seq')"], "Custom defaults"),
    (["ALTER TABLE v2_settings ENABLE ROW LEVEL SECURITY"], "Row-level security"),
    (["CREATE POLICY core_policy ON v2_settings USING (true)"], "Row policies"),
    (["CREATE COLLATION custom_collation (provider=libc, locale='C')",
      "ALTER TABLE v2_cases ALTER COLUMN reason TYPE VARCHAR(500) COLLATE custom_collation"], "Custom collations"),
    (["CREATE COLLATION custom_collation (provider=libc, locale='C')",
      "CREATE INDEX custom_collated_index ON v2_cases(reason COLLATE custom_collation)"], "Custom index collations"),
    (["CREATE ACCESS METHOD custom_heap TYPE TABLE HANDLER heap_tableam_handler",
      "ALTER TABLE v2_settings SET ACCESS METHOD custom_heap"], "Custom table access methods"),
])
async def test_custom_dependencies_fail_closed_before_backup_or_ddl(maintenance_database, statements, diagnostic):
    await seed_v1(maintenance_database)
    engine = maintenance_engine(maintenance_database)
    try:
        async with engine.begin() as connection:
            for statement in statements:
                await connection.execute(text(statement))
        async with engine.begin() as connection:
            before = await snapshot(connection)
            with pytest.raises(MigrationError, match=diagnostic):
                await pg.validate_defaults(connection)
            assert await snapshot(connection) == before
            assert await pg.inspect_postgres(connection) == 1
    finally:
        await engine.dispose()


async def test_real_failed_revision_rolls_back_ddl_marker_and_payload(maintenance_database, monkeypatch):
    await seed_v1(maintenance_database)
    engine = maintenance_engine(maintenance_database)
    apply_revision = pg._apply_revision

    async def fail_after_ddl(connection, revision):
        await apply_revision(connection, revision)
        raise RuntimeError("injected failure after PostgreSQL DDL")

    monkeypatch.setattr(pg, "_apply_revision", fail_after_ddl)
    try:
        async with engine.connect() as connection:
            before = await snapshot(connection)
            await connection.rollback()
        with pytest.raises(RuntimeError, match="injected failure"):
            async with engine.begin() as connection:
                await pg.lock_source(connection)
                await pg.migrate_postgres(connection)
        async with engine.connect() as connection:
            assert await pg.inspect_postgres(connection) == 1
            assert await snapshot(connection) == before
    finally:
        await engine.dispose()


@pytest.mark.parametrize("version", [0, 999])
async def test_real_unknown_marker_is_rejected_before_ddl(maintenance_database, version):
    await seed_v1(maintenance_database)
    engine = maintenance_engine(maintenance_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE v2_schema SET version=:version"), {"version": version})
        async with engine.connect() as connection:
            before = await snapshot(connection)
            await connection.rollback()
        with pytest.raises(RuntimeError, match="Unsupported schema"):
            async with engine.begin() as connection:
                await pg.lock_source(connection)
        async with engine.connect() as connection:
            assert await snapshot(connection) == before
    finally:
        await engine.dispose()


async def test_real_source_lock_excludes_writers_and_other_maintenance(maintenance_database):
    await seed_v1(maintenance_database)
    engine = maintenance_engine(maintenance_database)
    try:
        async with engine.begin() as holder:
            await pg.lock_source(holder)
            async with engine.begin() as competing:
                with pytest.raises(MigrationError, match="Another"):
                    await pg.lock_source(competing)
            async with engine.begin() as reader:
                assert await reader.scalar(text("SELECT count(*) FROM v2_settings")) == 2
            async with engine.connect() as writer:
                await writer.execute(text("SET lock_timeout='50ms'"))
                with pytest.raises(DBAPIError) as error:
                    await writer.execute(text("UPDATE v2_settings SET levels=true WHERE guild_id=11"))
                assert error.value.orig.sqlstate == "55P03"
                await writer.rollback()
        async with engine.begin() as writer:
            await writer.execute(text("UPDATE v2_settings SET levels=true WHERE guild_id=11"))
    finally:
        await engine.dispose()
