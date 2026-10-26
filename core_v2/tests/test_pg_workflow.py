"""Real pg_dump → transactional upgrade → pg_restore; disposable databases only."""
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from core_v2 import postgres_migrations as migration
from tools.launcher import pg_native as native, pg_workflow as pg
from test_postgres_migrations import (fresh_database, maintenance_database,
                                      maintenance_engine, seed_v1, snapshot)

# Imported pytest fixtures are deliberately available to pytest discovery here.
__all__ = ["fresh_database", "maintenance_database"]


def configure(root, url, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (root / ".env.v2").write_text("DATABASE_URL='" + url + "'\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def native_tools():
    import os
    import shutil
    if os.getenv("TEST_POSTGRES_MAINTENANCE_URL") and (not shutil.which("pg_dump") or not shutil.which("pg_restore")):
        pytest.fail("Native pg_dump/pg_restore are required for maintenance integration tests")


async def test_native_backup_upgrade_restore_preserves_two_guilds_and_indexes(tmp_path, fresh_database, monkeypatch):
    tmp_path = tmp_path / "Бот з пробілами"
    tmp_path.mkdir()
    async with fresh_database() as source, fresh_database() as target:
        await seed_v1(source)
        configure(tmp_path, source, monkeypatch)
        database = maintenance_engine(source)
        try:
            async with database.connect() as conn:
                before = await snapshot(conn)
            preview = await pg.plan(tmp_path)
            assert preview["current"] == 1 and preview["revisions"] == ["002_case_updated_at"]
            assert not (tmp_path / "backups").exists()
            report = await pg.maintain(tmp_path, upgrade=True, confirmed=True, expected_plan=preview)
            assert report["status"] == "applied"
            saved = tmp_path / report["backup"]
            assert native.verify_archive(saved)["schema_version"] == 1
            async with database.connect() as conn:
                after = await snapshot(conn)
                assert before | {"v2_schema": [(2,)]} == after
                assert (await conn.execute(text("SELECT created,updated FROM v2_cases"))).all() == [(1234.5, 1234.5)] * 2
            assert (await pg.maintain(tmp_path, upgrade=True, confirmed=True))["status"] == "unchanged"
            assert len(list(saved.parent.glob("*.dump"))) == 1
            assert (await pg.restore_empty(tmp_path, saved, target, confirmed=True))["status"] == "restored"
            recovered = maintenance_engine(target)
            try:
                async with recovered.connect() as conn:
                    assert await migration.inspect_postgres(conn) == 1
                    assert await snapshot(conn) == before
                    assert await conn.scalar(text("SELECT count(*) FROM pg_indexes WHERE indexname='preserve_case_index'")) == 1
            finally:
                await recovered.dispose()
            # Neither URL, password nor connection credentials are archived/reported.
            public = saved.with_suffix(saved.suffix + ".json").read_text() + (tmp_path / "data/v2/postgres-report.json").read_text()
            assert source not in public and target not in public
            assert json.loads((tmp_path / "data/v2/postgres-report.json").read_text())["status"] == "restored"
        finally:
            await database.dispose()


async def test_native_v2_snapshot_and_empty_restore_keep_actual_version(tmp_path, fresh_database, monkeypatch):
    async with fresh_database() as source, fresh_database() as target:
        await seed_v1(source)
        configure(tmp_path, source, monkeypatch)
        await pg.maintain(tmp_path, upgrade=True, confirmed=True)
        backup = await pg.maintain(tmp_path)
        saved = tmp_path / backup["backup"]
        assert native.verify_archive(saved)["schema_version"] == 2
        await pg.restore_empty(tmp_path, saved, target, confirmed=True)
        restored = maintenance_engine(target)
        try:
            async with restored.connect() as conn:
                assert await migration.inspect_postgres(conn) == 2
                assert (await conn.execute(text("SELECT created,updated FROM v2_cases"))).all() == [(1234.5, 1234.5)] * 2
        finally:
            await restored.dispose()


async def test_dump_failure_prevents_any_migration(tmp_path, maintenance_database, monkeypatch):
    await seed_v1(maintenance_database)
    configure(tmp_path, maintenance_database, monkeypatch)
    def fail(*a, **k):
        raise native.PostgresError("synthetic client failure with hidden credentials")
    monkeypatch.setattr(native, "dump", fail)
    with pytest.raises(pg.PostgresError):
        await pg.maintain(tmp_path, upgrade=True, confirmed=True)
    database = maintenance_engine(maintenance_database)
    try:
        async with database.connect() as conn:
            assert await migration.inspect_postgres(conn) == 1
            assert "updated" not in (await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='v2_cases'"))).scalars().all()
    finally:
        await database.dispose()
    assert json.loads((tmp_path / "data/v2/postgres-report.json").read_text())["status"] == "failed"


async def test_failed_ddl_rolls_back_but_keeps_recoverable_backup(tmp_path, maintenance_database, monkeypatch):
    await seed_v1(maintenance_database)
    configure(tmp_path, maintenance_database, monkeypatch)
    original = migration._apply_revision
    async def fail(connection, revision):
        await original(connection, revision)
        raise RuntimeError("synthetic failure after DDL")
    monkeypatch.setattr(migration, "_apply_revision", fail)
    with pytest.raises(pg.PostgresError):
        await pg.maintain(tmp_path, upgrade=True, confirmed=True)
    database = maintenance_engine(maintenance_database)
    try:
        async with database.connect() as conn:
            assert await migration.inspect_postgres(conn) == 1
            assert (await conn.scalar(text("SELECT count(*) FROM v2_cases"))) == 2
    finally:
        await database.dispose()
    backups = list((tmp_path / "backups/postgres").glob("*.dump"))
    assert len(backups) == 1 and native.verify_archive(backups[0])["schema_version"] == 1


async def test_configuration_change_during_dump_blocks_ddl_in_both_databases(tmp_path, fresh_database, monkeypatch):
    async with fresh_database() as source, fresh_database() as other:
        await seed_v1(source)
        await seed_v1(other)
        configure(tmp_path, source, monkeypatch)
        original = native.dump
        def change(*args):
            result = original(*args)
            (tmp_path / ".env.v2").write_text("DATABASE_URL='" + other + "'\n", encoding="utf-8")
            return result
        monkeypatch.setattr(native, "dump", change)
        with pytest.raises(pg.PostgresError):
            await pg.maintain(tmp_path, upgrade=True, confirmed=True)
        for url in (source, other):
            database = maintenance_engine(url)
            try:
                async with database.connect() as conn:
                    assert await migration.inspect_postgres(conn) == 1
            finally:
                await database.dispose()


async def test_nonempty_target_is_preserved_without_native_restore(tmp_path, fresh_database, monkeypatch):
    async with fresh_database() as source, fresh_database() as target:
        await seed_v1(source)
        await seed_v1(target)
        configure(tmp_path, source, monkeypatch)
        saved = tmp_path / (await pg.maintain(tmp_path))["backup"]
        reached = []
        monkeypatch.setattr(native, "restore", lambda *a: reached.append(True))
        with pytest.raises(pg.PostgresError):
            await pg.restore_empty(tmp_path, saved, target, confirmed=True)
        assert reached == []
        database = maintenance_engine(target)
        try:
            async with database.connect() as conn:
                assert await migration.inspect_postgres(conn) == 1
                assert await conn.scalar(text("SELECT count(*) FROM v2_settings")) == 2
        finally:
            await database.dispose()


async def test_stale_plan_is_refused_without_backup_or_ddl(tmp_path, maintenance_database, monkeypatch):
    await seed_v1(maintenance_database)
    configure(tmp_path, maintenance_database, monkeypatch)
    preview = await pg.plan(tmp_path)
    preview["source_id"] = "changed"
    with pytest.raises(pg.PostgresError):
        await pg.maintain(tmp_path, upgrade=True, confirmed=True, expected_plan=preview)
    assert not (tmp_path / "backups").exists()


async def test_unconfirmed_upgrade_does_not_connect_or_create_files(tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "connect", lambda *a: pytest.fail("Unconfirmed operation connected"))
    with pytest.raises(pg.PostgresError):
        await pg.maintain(tmp_path, upgrade=True)
    with pytest.raises(pg.PostgresError):
        await pg.restore_empty(tmp_path, Path("missing"), "not-a-url")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("ddl", [
    "CREATE TABLE inherited_xp () INHERITS (public.v2_xp)",
    "ALTER TABLE v2_settings ADD COLUMN custom_value INTEGER",
    "ALTER TABLE v2_cases ADD CONSTRAINT custom_check CHECK (created > 0)",
    "CREATE INDEX custom_expression ON v2_cases ((lower(reason)))",
    "CREATE FUNCTION public.custom_test() RETURNS boolean LANGUAGE SQL IMMUTABLE RETURN true",
    "CREATE TYPE public.custom_mood AS ENUM ('x'); ALTER TABLE v2_cases ALTER COLUMN reason TYPE public.custom_mood USING 'x'::public.custom_mood",
    "CREATE TABLE v2_future (id INTEGER)",
    "CREATE OPERATOR CLASS public.custom_text_ops FOR TYPE text USING btree AS "
    "OPERATOR 1 <, OPERATOR 2 <=, OPERATOR 3 =, OPERATOR 4 >=, OPERATOR 5 >, FUNCTION 1 bttextcmp(text,text); "
    "CREATE INDEX custom_ops_index ON v2_cases (reason public.custom_text_ops)",
])
async def test_custom_schema_is_refused_before_snapshot_and_ddl(tmp_path, maintenance_database, monkeypatch, ddl):
    await seed_v1(maintenance_database)
    configure(tmp_path, maintenance_database, monkeypatch)
    database = maintenance_engine(maintenance_database)
    try:
        async with database.begin() as conn:
            # asyncpg accepts one SQL command per prepared statement.
            for statement in ddl.split("; "):
                await conn.execute(text(statement))
        monkeypatch.setattr(native, "dump", lambda *a: pytest.fail("Unsupported source reached pg_dump"))
        with pytest.raises(pg.PostgresError):
            await pg.maintain(tmp_path, upgrade=True, confirmed=True)
        async with database.connect() as conn:
            assert await migration.inspect_postgres(conn) == 1
        assert not (tmp_path / "backups").exists()
    finally:
        await database.dispose()


async def test_commit_connection_failure_is_reported_as_unknown_not_rollback(tmp_path, maintenance_database, monkeypatch):
    from sqlalchemy import event
    await seed_v1(maintenance_database)
    configure(tmp_path, maintenance_database, monkeypatch)
    database = maintenance_engine(maintenance_database)
    @event.listens_for(database.sync_engine, "commit")
    def commit_failure(connection):
        raise RuntimeError("simulated lost connection during commit")
    monkeypatch.setattr(pg, "connect", lambda *a: database)
    with pytest.raises(pg.PostgresError) as error:
        await pg.maintain(tmp_path, upgrade=True, confirmed=True)
    assert "відкочено" not in str(error.value)
    report = json.loads((tmp_path / "data/v2/postgres-report.json").read_text())
    assert report["status"] == "unknown_commit"
    assert native.verify_archive(tmp_path / report["backup"])["schema_version"] == 1


async def test_report_failure_after_commit_does_not_claim_migration_failed(tmp_path, maintenance_database, monkeypatch):
    await seed_v1(maintenance_database)
    configure(tmp_path, maintenance_database, monkeypatch)
    write = pg.write_report
    def fail_after_commit(root, report):
        if report["status"] == "applied":
            raise OSError("synthetic report disk failure")
        write(root, report)
    monkeypatch.setattr(pg, "write_report", fail_after_commit)
    with pytest.raises(pg.PostgresError, match="завершена"):
        await pg.maintain(tmp_path, upgrade=True, confirmed=True)
    database = maintenance_engine(maintenance_database)
    try:
        async with database.connect() as conn:
            assert await migration.inspect_postgres(conn) == 2
    finally:
        await database.dispose()


def test_ambient_tls_policy_is_not_silently_downgraded(tmp_path, monkeypatch):
    configure(tmp_path, "postgresql+asyncpg://unit:private@localhost/yeboy_test", monkeypatch)
    monkeypatch.setenv("PGSSLMODE", "verify-full")
    with pytest.raises(pg.PostgresError):
        pg.configured_url(tmp_path)


async def test_cancellation_waits_for_native_worker_before_releasing_caller():
    import asyncio
    import threading
    started, release = threading.Event(), threading.Event()
    def worker():
        started.set()
        assert release.wait(timeout=5)
    task = asyncio.create_task(pg.native_call(worker))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
