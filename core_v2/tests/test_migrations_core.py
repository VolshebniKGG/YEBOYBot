"""Real additive upgrades and transaction rollback without Discord or user databases."""
from contextlib import closing
from dataclasses import FrozenInstanceError
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, select

from core_v2 import db as database_module
from core_v2 import migrations as m
from core_v2.db import CURRENT_SCHEMA_VERSION, Database, MigrationRequired, cases, meta, required_columns


def legacy_metadata():
    legacy = MetaData()
    for table in meta.sorted_tables:
        Table(table.name, legacy, *(Column(column.name, column.type,
              primary_key=column.primary_key, nullable=column.nullable)
              for column in table.columns if not (table.name == "v2_cases" and column.name == "updated")))
    return legacy


def create_legacy_v1(path):
    """Build the real previous table shape, with data in every guild-scoped table."""
    path = path.resolve()
    engine = create_engine("sqlite:///" + str(path))
    try:
        legacy_metadata().create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO v2_schema VALUES (1)")
            for guild in (11, 22):
                connection.exec_driver_sql("INSERT INTO v2_settings VALUES (?,0,0,?,?,?)", (guild, 10, 20, 30))
                connection.exec_driver_sql("INSERT INTO v2_xp VALUES (?,101,15,5,100,200)", (guild,))
                connection.exec_driver_sql("INSERT INTO v2_cases VALUES (?,?,101,202,'warn','Зберегти',1234.5,'applied')",
                                           (str(guild), guild))
                connection.exec_driver_sql("INSERT INTO v2_jobs VALUES (?,?,101,303,'нагадування',1000,0,0,'pending')",
                                           (str(guild), guild))
                connection.exec_driver_sql("INSERT INTO v2_role_panels VALUES (?,?,404)", (str(guild), guild))
            connection.exec_driver_sql("CREATE INDEX preserve_case_index ON v2_cases(guild_id, created)")
    finally:
        engine.dispose()
    return path


def payload(connection):
    result = {}
    for name, columns in required_columns(1).items():
        if name == "v2_schema":
            continue
        fields = ",".join('"' + column + '"' for column in sorted(columns))
        result[name] = connection.execute(f'SELECT {fields} FROM "{name}" ORDER BY 1,2').fetchall()
    return result


@pytest.fixture
def legacy(tmp_path):
    return create_legacy_v1(tmp_path / "База з пробілами.db")


def test_plan_is_read_only_and_reports_actual_revision(legacy):
    before = legacy.read_bytes()
    assert m.plan_sqlite(legacy) == {"current": 1, "target": 2,
        "revisions": ["002_case_updated_at"], "requires_backup": True}
    assert legacy.read_bytes() == before


def test_plan_missing_database_never_creates_it(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(m.MigrationError):
        m.plan_sqlite(missing)
    assert not missing.exists()


def test_upgrade_preserves_all_payload_and_backfills_timestamp(legacy):
    with closing(sqlite3.connect(legacy)) as connection:
        before = payload(connection)
        index = connection.execute("SELECT sql FROM sqlite_master WHERE name='preserve_case_index'").fetchone()
        connection.execute("BEGIN IMMEDIATE")
        assert m.migrate_sqlite(connection) == ["002_case_updated_at"]
        connection.commit()
        assert payload(connection) == before
        assert connection.execute("SELECT created,updated FROM v2_cases").fetchall() == [(1234.5, 1234.5)] * 2
        assert connection.execute("SELECT version FROM v2_schema").fetchall() == [(CURRENT_SCHEMA_VERSION,)]
        assert connection.execute("SELECT sql FROM sqlite_master WHERE name='preserve_case_index'").fetchone() == index
        assert m.inspect_sqlite(connection) == 2


def test_failed_revision_rolls_back_ddl_marker_and_payload(legacy, monkeypatch):
    before = legacy.read_bytes()
    apply_revision = m._apply_revision
    def fail_after_ddl(connection, revision):
        apply_revision(connection, revision)
        raise RuntimeError("injected migration failure")
    monkeypatch.setattr(m, "_apply_revision", fail_after_ddl)
    with closing(sqlite3.connect(legacy)) as connection:
        original = payload(connection)
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match="injected migration"):
            m.migrate_sqlite(connection)
        connection.rollback()
        assert m.inspect_sqlite(connection) == 1
        assert payload(connection) == original
        assert "updated" not in {row[1] for row in connection.execute("PRAGMA table_info(v2_cases)")}
    assert legacy.read_bytes() == before


def test_migration_requires_explicit_transaction(legacy):
    before = legacy.read_bytes()
    with closing(sqlite3.connect(legacy)) as connection:
        with pytest.raises(m.MigrationError, match="transaction"):
            m.migrate_sqlite(connection)
        assert not connection.in_transaction
    assert legacy.read_bytes() == before


def test_repeated_upgrade_is_noop(legacy):
    with closing(sqlite3.connect(legacy)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        m.migrate_sqlite(connection)
        connection.commit()
        before = legacy.read_bytes()
        original = payload(connection)
        connection.execute("BEGIN IMMEDIATE")
        assert m.migrate_sqlite(connection) == []
        connection.commit()
        assert payload(connection) == original
    assert legacy.read_bytes() == before
    assert m.plan_sqlite(legacy)["requires_backup"] is False


@pytest.mark.parametrize("target", [0, 1, 3, True, "2"])
def test_unsupported_target_or_downgrade_never_writes(legacy, target):
    before = legacy.read_bytes()
    with pytest.raises(m.MigrationError):
        m.plan_sqlite(legacy, target=target)
    assert legacy.read_bytes() == before


def test_unknown_source_is_not_adopted(legacy):
    with closing(sqlite3.connect(legacy)) as connection:
        connection.execute("UPDATE v2_schema SET version=999")
        connection.commit()
    before = legacy.read_bytes()
    with pytest.raises(RuntimeError, match="Unsupported schema"):
        m.plan_sqlite(legacy)
    assert legacy.read_bytes() == before


def test_fresh_file_is_bootstrapped_only_by_runtime(tmp_path):
    path = tmp_path / "fresh.db"
    with closing(sqlite3.connect(path)) as connection:
        assert m.inspect_sqlite(connection) == 0
    with pytest.raises(m.MigrationError, match="bootstrap"):
        m.plan_sqlite(path)


def test_ambiguous_partially_upgraded_database_refused(legacy):
    with closing(sqlite3.connect(legacy)) as connection:
        connection.execute("ALTER TABLE v2_cases ADD COLUMN updated FLOAT")
        connection.commit()
    before = legacy.read_bytes()
    with pytest.raises(RuntimeError, match="ambiguous"):
        m.plan_sqlite(legacy)
    assert legacy.read_bytes() == before


def test_registry_is_ordered_immutable_and_detects_missing_chain(monkeypatch):
    assert isinstance(m.REVISIONS, tuple)
    with pytest.raises(FrozenInstanceError):
        m.REVISIONS[0].target = 999
    monkeypatch.setattr(m, "REVISIONS", ())
    with pytest.raises(m.MigrationError, match="chain"):
        m.migration_path(1)


async def test_runtime_refuses_legacy_before_wal_or_ddl(legacy):
    before = legacy.read_bytes()
    database = Database("sqlite+aiosqlite:///" + str(legacy))
    try:
        with pytest.raises(MigrationRequired):
            await database.initialize()
    finally:
        await database.close()
    assert legacy.read_bytes() == before
    assert not legacy.with_suffix(".db-wal").exists()
    assert not legacy.with_suffix(".db-shm").exists()


async def test_runtime_reopens_explicitly_migrated_database(legacy):
    with closing(sqlite3.connect(legacy)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        m.migrate_sqlite(connection)
        connection.commit()
    database = Database("sqlite+aiosqlite:///" + str(legacy))
    try:
        await database.initialize()
        assert (await database.get_settings(11))["levels"] is False
        assert (await database.rank(22, 101))["text_xp"] == 15
    finally:
        await database.close()


async def test_new_case_lifecycle_records_updated_without_cross_guild_write(tmp_path, monkeypatch):
    database = Database("sqlite+aiosqlite:///" + str(tmp_path / "fresh.db"))
    try:
        await database.initialize()
        monkeypatch.setattr(database_module, "time", SimpleNamespace(time=lambda: 10))
        case_id = await database.create_case(11, 101, 202, "timeout", "synthetic")
        async with database.engine.connect() as connection:
            row = (await connection.execute(select(cases).where(cases.c.id == case_id))).mappings().one()
            assert row["created"] == row["updated"] == 10
        monkeypatch.setattr(database_module, "time", SimpleNamespace(time=lambda: 20))
        assert not await database.finish_case(22, case_id, "applied")
        assert await database.finish_case(11, case_id, "applied")
        warning = await database.create_case(11, 101, 202, "warn", "synthetic", status="applied")
        monkeypatch.setattr(database_module, "time", SimpleNamespace(time=lambda: 30))
        assert await database.revoke_warning(11, warning)
        async with database.engine.connect() as connection:
            rows = (await connection.execute(select(cases.c.id, cases.c.updated))).all()
            assert dict(rows) == {case_id: 20, warning: 30}
    finally:
        await database.close()
