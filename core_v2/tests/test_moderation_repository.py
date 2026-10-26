"""Real SQL case reservation/history tests; no Discord calls or production data."""
import asyncio
import os
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from core_v2 import db as database_module
from core_v2.db import Database, cases


@pytest.fixture(params=["sqlite", "postgres"])
async def repository(request, tmp_path):
    if request.param == "sqlite":
        database = Database("sqlite+aiosqlite:///" + str(tmp_path / "audit з пробілами.db"))
        try:
            await database.initialize()
            yield database
        finally:
            await database.close()
        return
    configured = os.getenv("TEST_DATABASE_URL", "")
    if not configured.startswith("postgresql"):
        pytest.skip("Disposable PostgreSQL not configured")
    url = make_url(configured)
    if url.drivername != "postgresql+asyncpg" or url.database != "yeboy_test":
        raise RuntimeError("Moderation SQL tests require disposable yeboy_test")
    name = "moderation_test_" + uuid.uuid4().hex
    admin = create_async_engine(url)
    database = Database(url.render_as_string(hide_password=False))
    await database.close()
    database.engine = create_async_engine(url, connect_args={"server_settings": {"search_path": name}})
    created = False
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{name}"'))
            created = True
        await database.initialize()
        yield database
    finally:
        await database.close()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
        await admin.dispose()


def request_values(**changes):
    result = dict(guild_id=11, case_id="1" * 32, user_id=22, actor_id=33,
                  action="kick", reason="Змістовна причина")
    result.update(changes)
    return result


@pytest.mark.parametrize("action", ["kick", "ban", "unban"])
async def test_concurrent_claim_has_only_one_owner_and_one_persistent_row(repository, action):
    outcomes = await asyncio.gather(*(repository.claim_case(**request_values(action=action)) for _ in range(20)))
    assert sum(created for _, created in outcomes) == 1
    assert all(row["id"] == "1" * 32 and row["status"] == "pending" and row["action"] == action
               for row, _ in outcomes)
    rows = await repository.case_history(11)
    assert len(rows) == 1
    assert rows[0]["created"] == rows[0]["updated"] > 0


@pytest.mark.parametrize("action", ["kick", "ban", "unban"])
async def test_replay_preserves_finished_status_reason_and_timestamps(repository, action):
    first, owner = await repository.claim_case(**request_values(action=action, reason="  Змістовна причина  "))
    assert owner
    assert first["reason"] == "Змістовна причина"
    assert await repository.finish_case(11, first["id"], "applied") == 1
    before = (await repository.case_history(11))[0]
    replay, owner = await repository.claim_case(**request_values(action=action))
    assert not owner
    assert replay == before
    assert replay["status"] == "applied"
    assert replay["updated"] >= first["updated"]
    assert await repository.finish_case(22, first["id"], "failed") == 0
    assert await repository.finish_case(11, first["id"], "failed") == 0
    assert (await repository.case_history(11))[0] == before


@pytest.mark.parametrize("change", [
    {"guild_id": 44}, {"user_id": 44}, {"actor_id": 44}, {"reason": "Інша причина"},
])
@pytest.mark.parametrize("action", ["kick", "ban", "unban"])
async def test_conflicting_request_never_overwrites_or_exposes_original(repository, change, action):
    original, _ = await repository.claim_case(**request_values(action=action))
    with pytest.raises(ValueError, match="different request"):
        await repository.claim_case(**request_values(action=action, **change))
    assert await repository.case_history(11) == [original]
    assert await repository.case_history(44) == []


@pytest.mark.parametrize("original_action,replayed_action", [
    ("kick", "ban"), ("ban", "unban"), ("unban", "ban"),
])
async def test_same_interaction_cannot_change_its_action(repository, original_action, replayed_action):
    original, _ = await repository.claim_case(**request_values(action=original_action))
    with pytest.raises(ValueError, match="different request"):
        await repository.claim_case(**request_values(action=replayed_action))
    assert await repository.case_history(11) == [original]


async def test_concurrent_different_actions_cannot_share_one_interaction(repository):
    async def claim(action):
        try:
            row, created = await repository.claim_case(**request_values(action=action))
            return action, row, created
        except ValueError:
            return action, None, False
    outcomes = await asyncio.gather(*(claim(action) for action in ("kick", "ban", "unban")))
    accepted = [(action, row) for action, row, created in outcomes if created]
    assert len(accepted) == 1
    action, row = accepted[0]
    assert row["action"] == action and row["status"] == "pending"
    assert sum(row is None for _, row, _ in outcomes) == 2
    assert await repository.case_history(11) == [row]


async def test_claimed_kick_ban_unban_history_stays_in_its_guild(repository):
    for guild_id in (11, 44):
        for number, action in enumerate(("kick", "ban", "unban"), 1):
            await repository.claim_case(**request_values(
                guild_id=guild_id, case_id=f"{guild_id:016x}{number:016x}", action=action))
    for guild_id in (11, 44):
        rows = await repository.case_history(guild_id, user_id=22)
        assert len(rows) == 3
        assert {row["action"] for row in rows} == {"kick", "ban", "unban"}
        assert {row["guild_id"] for row in rows} == {guild_id}
        assert all(row["status"] == "pending" for row in rows)


async def test_history_is_guild_scoped_and_filters_former_member_and_state(repository, monkeypatch):
    monkeypatch.setattr(database_module.time, "time", lambda: 1000.0)
    own = []
    for guild_id, user_id, status in [(11, 22, "pending"), (11, 22, "applied"),
                                      (11, 44, "failed"), (55, 22, "pending")]:
        case_id = await repository.create_case(guild_id, user_id, 33, "kick", "reason", status=status)
        if guild_id == 11:
            own.append(case_id)
    rows = await repository.case_history(11, user_id=22)
    assert len(rows) == 2
    assert {row["guild_id"] for row in rows} == {11}
    assert {row["user_id"] for row in rows} == {22}
    assert [row["status"] for row in await repository.case_history(11, user_id=22, status="pending")] == ["pending"]
    assert [row["status"] for row in await repository.case_history(11, status="failed")] == ["failed"]
    assert await repository.case_history(11, user_id=99) == []
    assert await repository.case_history(99) == []


async def test_history_pagination_has_stable_id_tie_break_and_limit(repository, monkeypatch):
    monkeypatch.setattr(database_module.time, "time", lambda: 1000.0)
    for number in range(13):
        await repository.claim_case(**request_values(case_id=f"{number:032x}"))
    pages = [await repository.case_history(11, page=page, page_size=5) for page in (1, 2, 3, 4)]
    assert [len(page) for page in pages] == [5, 5, 3, 0]
    combined = [row["id"] for page in pages for row in page]
    assert combined == [f"{number:032x}" for number in reversed(range(13))]
    assert len({*combined}) == 13
    assert len(await repository.case_history(11, page_size=10)) == 10


async def test_reason_is_parameterized_and_never_becomes_sql(repository):
    reason = "'; DROP TABLE v2_cases; --"
    row, created = await repository.claim_case(**request_values(reason=reason))
    assert created and row["reason"] == reason
    assert (await repository.case_history(11))[0]["reason"] == reason
    async with repository.engine.connect() as connection:
        assert (await connection.execute(select(cases.c.id))).scalars().all() == ["1" * 32]


async def test_history_is_read_only_and_does_not_bootstrap_tables(repository):
    observed = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        observed.append(statement.lstrip().upper())
    event.listen(repository.engine.sync_engine, "before_cursor_execute", capture)
    try:
        assert await repository.case_history(11) == []
    finally:
        event.remove(repository.engine.sync_engine, "before_cursor_execute", capture)
    assert observed
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP")) for sql in observed)


@pytest.mark.parametrize("change", [
    {"guild_id": 0}, {"guild_id": -1}, {"guild_id": True}, {"guild_id": 2**63},
    {"user_id": "22"}, {"user_id": 0}, {"actor_id": False}, {"actor_id": 2**63},
    {"case_id": "A" * 32}, {"case_id": "0" * 31}, {"case_id": "g" * 32},
    {"case_id": 123}, {"action": "clear"}, {"action": None}, {"action": []},
    {"action": {}}, {"action": 1}, {"reason": " "}, {"reason": None},
    {"reason": "x" * 501}, {"reason": "Причина\0з NUL"},
])
async def test_invalid_claim_refused_before_database_transaction(change):
    # No real engine is needed: validation must finish before opening a transaction.
    database = SimpleNamespace(engine=SimpleNamespace(begin=lambda: pytest.fail("Invalid request reached SQL")))
    with pytest.raises(ValueError):
        await Database.claim_case(database, **request_values(**change))


@pytest.mark.parametrize("change", [
    {"guild_id": 0}, {"guild_id": True}, {"guild_id": 2**63}, {"user_id": False},
    {"user_id": 2**63}, {"status": "unknown"}, {"status": ["pending"]},
    {"page": 0}, {"page": 1001}, {"page": True}, {"page_size": 0},
    {"page_size": 11}, {"page_size": True},
])
async def test_invalid_history_refused_before_database_connection(change):
    values = dict(guild_id=11, user_id=None, status=None, page=1, page_size=5)
    values.update(change)
    database = SimpleNamespace(engine=SimpleNamespace(connect=lambda: pytest.fail("Invalid read reached SQL")))
    with pytest.raises(ValueError):
        await Database.case_history(database, **values)
