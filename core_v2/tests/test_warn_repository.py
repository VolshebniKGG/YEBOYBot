"""Atomic local warning records on disposable SQLite/PostgreSQL databases."""
import asyncio
import os
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from core_v2 import db as module
from core_v2.db import Database, WarningConflict, cases


@pytest.fixture(params=["sqlite", "postgres"])
async def repository(request, tmp_path):
    if request.param == "sqlite":
        database = Database("sqlite+aiosqlite:///" + str(tmp_path / "Попередження з пробілами.db"))
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
        raise RuntimeError("Warning SQL tests require disposable yeboy_test")
    namespace = "warning_test_" + uuid.uuid4().hex
    admin = create_async_engine(url)
    database = Database(url.render_as_string(hide_password=False))
    await database.close()
    database.engine = create_async_engine(url, connect_args={"server_settings": {"search_path": namespace}})
    created = False
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
            created = True
        await database.initialize()
        yield database
    finally:
        await database.close()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{namespace}" CASCADE'))
        await admin.dispose()


def request_values(**changes):
    values = dict(guild_id=11, case_id=f"{11:016x}{99:016x}",
                  user_id=22, actor_id=33, reason="Обґрунтоване попередження")
    values.update(changes)
    return values


async def rows(database):
    async with database.engine.connect() as connection:
        return [dict(row) for row in (await connection.execute(select(cases))).mappings()]


async def test_warning_is_committed_as_applied_with_one_timestamp(repository, monkeypatch):
    monkeypatch.setattr(module.time, "time", lambda: 1700000000.0)
    record, created = await repository.record_warning(**request_values())
    assert created and record == {
        "id": request_values()["case_id"], "guild_id": 11, "user_id": 22,
        "actor_id": 33, "reason": request_values()["reason"], "action": "warn",
        "created": 1700000000.0, "updated": 1700000000.0, "status": "applied",
    }
    assert await rows(repository) == [record]
    assert await repository.finish_case(11, record["id"], "failed") == 0
    assert await repository.warnings(11, 22) == [record]


async def test_concurrent_duplicate_delivery_commits_only_one_warning(repository):
    results = await asyncio.gather(*(repository.record_warning(**request_values()) for _ in range(20)))
    assert sum(created for _, created in results) == 1
    assert all(row == results[0][0] for row, _ in results)
    assert await rows(repository) == [results[0][0]]
    assert await repository.warnings(11, 22) == [results[0][0]]


@pytest.mark.parametrize("state", ["applied", "revoked", "failed", "pending", "unknown_legacy"])
async def test_replay_preserves_any_existing_state_and_timestamps(repository, monkeypatch, state):
    record, _ = await repository.record_warning(**request_values())
    async with repository.engine.begin() as connection:
        await connection.execute(update(cases).where(cases.c.id == record["id"]).values(
            status=state, created=100.0, updated=200.0))
    expected = dict(record, status=state, created=100.0, updated=200.0)
    monkeypatch.setattr(module.time, "time", lambda: 300.0)
    replay, created = await repository.record_warning(**request_values())
    assert not created and replay == expected
    assert await rows(repository) == [expected]


@pytest.mark.parametrize("change", [
    {"guild_id": 44}, {"user_id": 44}, {"actor_id": 44}, {"reason": "Інша причина"},
])
async def test_conflicting_input_never_overwrites_or_exposes_original(repository, change):
    record, _ = await repository.record_warning(**request_values())
    with pytest.raises(WarningConflict) as error:
        await repository.record_warning(**request_values(**change))
    assert record["reason"] not in str(error.value)
    assert await rows(repository) == [record]
    assert await repository.case_history(44) == []


async def test_same_interaction_with_different_payload_has_only_one_winner(repository):
    async def attempt(number):
        try:
            return await repository.record_warning(**request_values(reason=f"Причина {number}"))
        except WarningConflict:
            return None
    results = await asyncio.gather(*(attempt(number) for number in range(8)))
    accepted = [result for result in results if result is not None]
    assert len(accepted) == 1 and accepted[0][1] is True
    assert await rows(repository) == [accepted[0][0]]


@pytest.mark.parametrize("action", ["kick", "ban", "unban"])
async def test_existing_api_case_cannot_turn_into_warning(repository, action):
    record, _ = await repository.claim_case(**request_values(), action=action)
    with pytest.raises(WarningConflict):
        await repository.record_warning(**request_values())
    assert await rows(repository) == [record]
    assert await repository.warnings(11, 22) == []


@pytest.mark.parametrize("action", ["kick", "ban", "unban"])
async def test_warning_cannot_turn_into_api_case(repository, action):
    record, _ = await repository.record_warning(**request_values())
    with pytest.raises(ValueError):
        await repository.claim_case(**request_values(), action=action)
    assert await rows(repository) == [record]


async def test_simultaneous_warning_and_api_claims_cannot_share_one_interaction(repository):
    async def attempt(action):
        try:
            if action == "warn":
                return await repository.record_warning(**request_values())
            return await repository.claim_case(**request_values(), action=action)
        except ValueError:
            return None
    results = await asyncio.gather(*(attempt(action) for action in ("warn", "kick", "ban", "unban")))
    accepted = [result for result in results if result is not None]
    assert len(accepted) == 1 and accepted[0][1] is True
    record = accepted[0][0]
    assert record["status"] == ("applied" if record["action"] == "warn" else "pending")
    assert await rows(repository) == [record]


async def test_warning_scope_keeps_two_guilds_independent(repository):
    saved = {}
    for guild_id in (11, 44):
        record, _ = await repository.record_warning(**request_values(
            guild_id=guild_id, case_id=f"{guild_id:016x}{99:016x}"))
        saved[guild_id] = record
    for guild_id in (11, 44):
        assert await repository.warnings(guild_id, 22) == [saved[guild_id]]
        assert await repository.case_history(guild_id, user_id=22) == [saved[guild_id]]
    assert await repository.revoke_warning(44, saved[11]["id"]) == 0
    assert await repository.warnings(11, 22) == [saved[11]]


async def test_legacy_uuid_warning_and_revocation_remain_compatible(repository):
    legacy_id = await repository.create_case(11, 22, 33, "warn", "Раніше збережено", status="applied")
    original = (await repository.warnings(11, 22))[0]
    assert original["id"] == legacy_id
    current, _ = await repository.record_warning(**request_values())
    assert {row["id"] for row in await repository.warnings(11, 22)} == {legacy_id, current["id"]}
    assert await repository.revoke_warning(11, current["id"]) == 1
    assert await repository.revoke_warning(11, current["id"]) == 0
    assert await repository.warnings(11, 22) == [original]
    before = (await repository.case_history(11, status="revoked"))[0]
    replay, created = await repository.record_warning(**request_values())
    assert not created and replay == before and replay["status"] == "revoked"
    assert await repository.warnings(11, 22) == [original]


async def test_failure_after_insert_rolls_back_the_entire_warning(repository):
    def fail_selection(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "v2_cases" in statement:
            raise RuntimeError("Synthetic failure after INSERT")
    event.listen(repository.engine.sync_engine, "before_cursor_execute", fail_selection)
    try:
        with pytest.raises(RuntimeError, match="Synthetic"):
            await repository.record_warning(**request_values())
    finally:
        event.remove(repository.engine.sync_engine, "before_cursor_execute", fail_selection)
    assert await rows(repository) == []
    record, created = await repository.record_warning(**request_values())
    assert created and record["status"] == "applied"


@pytest.mark.parametrize("reason", [
    "  Обґрунтована причина  ", "😀" * 500,
    "'); DROP TABLE v2_cases; --", "@everyone **перевірена причина**",
], ids=["trimmed", "maximum-unicode", "sql-injection-text", "discord-formatting"])
async def test_valid_reason_is_preserved_without_truncation_or_sql_interpolation(repository, reason):
    record, _ = await repository.record_warning(**request_values(reason=reason))
    assert record["reason"] == reason.strip()
    assert await repository.warnings(11, 22) == [record]


@pytest.mark.parametrize("field", ["guild_id", "user_id", "actor_id"])
@pytest.mark.parametrize("value", [True, 0, -1, 2**63, "11", None])
async def test_invalid_scope_is_rejected_before_opening_sql(field, value):
    database = SimpleNamespace(engine=SimpleNamespace(begin=lambda: pytest.fail("Invalid scope opened SQL")))
    with pytest.raises(ValueError):
        await Database.record_warning(database, **request_values(**{field: value}))


@pytest.mark.parametrize("case_id", [None, 42, "", "A" * 32, "0" * 31, "0" * 33,
                                    "０" * 32, "0" * 32 + "\n", "';DELETE FROM v2_cases;--"])
async def test_invalid_case_id_is_rejected_before_opening_sql(case_id):
    database = SimpleNamespace(engine=SimpleNamespace(begin=lambda: pytest.fail("Invalid key opened SQL")))
    with pytest.raises(ValueError):
        await Database.record_warning(database, **request_values(case_id=case_id))


@pytest.mark.parametrize("reason", [None, 1, [], "", " ", "x" * 501, "a\0b", "a\nb",
                                   "a\tb", "a\x1fb", "a\x7fb", "a\x85b", "a\x9fb",
                                   "a\ud800b", "a\udfffb"],
                         ids=["none", "integer", "list", "empty", "blank", "too-long", "nul",
                              "newline", "tab", "c0", "del", "c1", "c1-end", "high-surrogate", "low-surrogate"])
async def test_invalid_reason_is_rejected_before_opening_sql(reason):
    database = SimpleNamespace(engine=SimpleNamespace(begin=lambda: pytest.fail("Invalid reason opened SQL")))
    with pytest.raises(ValueError):
        await Database.record_warning(database, **request_values(reason=reason))
