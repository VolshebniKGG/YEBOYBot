"""Persistent, scoped reward rules and explicit v2-to-v3 upgrades."""
import asyncio
from contextlib import closing
import sqlite3

import pytest
from sqlalchemy import create_engine, text

from core_v2.db import Database, MigrationRequired, required_columns
from core_v2 import migrations
from test_migrations_core import legacy_metadata
from test_voice_xp import repository as _repository

repository = _repository


async def test_rewards_are_scoped_ordered_upserted_and_deleted(repository):
    db = repository
    await db.set_level_reward(11, "voice", 3, 91)
    await db.set_level_reward(11, "text", 2, 92)
    await db.set_level_reward(22, "text", 2, 93)
    await db.set_level_reward(11, "text", 2, 94)
    assert await db.level_rewards(11) == [
        {"guild_id": 11, "kind": "text", "level": 2, "role_id": 94},
        {"guild_id": 11, "kind": "voice", "level": 3, "role_id": 91}]
    assert not await db.delete_level_reward(22, "voice", 3)
    assert await db.delete_level_reward(11, "text", 2)
    assert not await db.delete_level_reward(11, "text", 2)
    assert (await db.level_rewards(22))[0]["role_id"] == 93


async def test_concurrent_cap_and_update_at_limit(repository):
    results = await asyncio.gather(*(repository.set_level_reward(11, "text", level, level + 100)
                                    for level in range(2, 32)), return_exceptions=True)
    assert sum(result is None for result in results) == 25
    assert sum(isinstance(result, ValueError) for result in results) == 5
    rows = await repository.level_rewards(11)
    assert len(rows) == 25
    await repository.set_level_reward(11, rows[0]["kind"], rows[0]["level"], 999)
    assert len(await repository.level_rewards(11)) == 25
    await repository.set_level_reward(22, "voice", 2, 123)
    assert len(await repository.level_rewards(22)) == 1


@pytest.mark.parametrize("args", [
    (True, "text", 2, 9), (0, "text", 2, 9), (2**63, "text", 2, 9),
    (1, None, 2, 9), (1, [], 2, 9), (1, "total", 2, 9),
    (1, "text", True, 9), (1, "text", 1, 9), (1, "text", 1001, 9),
    (1, "text", None, 9), (1, "text", 2, None), (1, "text", 2, True),
    (1, "text", 2, 0), (1, "text", 2, 2**63)])
async def test_invalid_inputs_never_write(repository, args):
    with pytest.raises(ValueError):
        await repository.set_level_reward(*args)
    assert await repository.level_rewards(1) == []


def seed_v2(path):
    engine = create_engine("sqlite:///" + str(path))
    try:
        legacy_metadata().create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE v2_cases ADD COLUMN updated FLOAT NOT NULL DEFAULT 0"))
            conn.execute(text("INSERT INTO v2_schema VALUES (2)"))
            conn.execute(text("INSERT INTO v2_xp VALUES (11,22,1234,456,100,200)"))
    finally:
        engine.dispose()


async def test_real_v2_upgrade_preserves_xp_and_reopens_rewards(tmp_path):
    path = tmp_path / "v2.db"
    seed_v2(path)
    original = path.read_bytes()
    db = Database("sqlite+aiosqlite:///" + str(path))
    try:
        with pytest.raises(MigrationRequired):
            await db.initialize()
    finally:
        await db.close()
    assert path.read_bytes() == original
    assert migrations.plan_sqlite(path)["revisions"] == ["003_level_rewards"]
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert migrations.migrate_sqlite(conn) == ["003_level_rewards"]
        conn.commit()
        assert conn.execute("SELECT * FROM v2_xp").fetchall() == [(11, 22, 1234, 456, 100, 200)]
    db = Database("sqlite+aiosqlite:///" + str(path))
    try:
        await db.initialize()
        await db.set_level_reward(11, "text", 2, 77)
    finally:
        await db.close()
    db = Database("sqlite+aiosqlite:///" + str(path))
    try:
        await db.initialize()
        assert (await db.level_rewards(11))[0]["role_id"] == 77
    finally:
        await db.close()


def test_v2_reward_ddl_rolls_back_and_partial_upgrade_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "v2.db"
    seed_v2(path)
    original = path.read_bytes()
    apply = migrations._apply_revision
    def interrupted(conn, revision):
        apply(conn, revision)
        raise RuntimeError("simulated interruption")
    monkeypatch.setattr(migrations, "_apply_revision", interrupted)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match="interruption"):
            migrations.migrate_sqlite(conn)
        conn.rollback()
        assert migrations.inspect_sqlite(conn) == 2
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='v2_level_rewards'").fetchall()
    assert path.read_bytes() == original
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE v2_level_rewards (guild_id BIGINT)")
        conn.commit()
    with pytest.raises(RuntimeError, match="partially migrated"):
        migrations.plan_sqlite(path)


def test_old_schema_requirements_remain_old():
    assert "v2_level_rewards" not in required_columns(1)
    assert "v2_level_rewards" not in required_columns(2)
    assert required_columns(3)["v2_level_rewards"] == {"guild_id", "kind", "level", "role_id"}
