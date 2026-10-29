"""Real SQLite/PostgreSQL manual clear receipts; no Discord or production data."""
import asyncio
from dataclasses import replace
import os
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from core_v2.clear_plan import ClearPlan, request_key
from core_v2.clear_repository import ClearRepository
from core_v2 import clear_repository as module
from core_v2.db import Database, jobs

NOW = 1_800_000_000.0
GUILD, ACTOR, CHANNEL, REQUEST = 110, 220, 330, 880


def plan(**changes):
    data = dict(id=request_key(GUILD, REQUEST), guild_id=GUILD, actor_id=ACTOR,
                channel_id=CHANNEL, request_id=REQUEST, reason="Обґрунтована причина",
                requested=3, message_ids=(800, 799), expires=NOW + 60, skipped_pinned=1)
    data.update(changes)
    return ClearPlan(**data)


async def get(repository, value=None):
    value = plan() if value is None else value
    return await repository.get(value.guild_id, value.actor_id, value.channel_id, value.id)


async def claim(repository, value=None, now=NOW):
    value = plan() if value is None else value
    return await repository.claim(value.guild_id, value.actor_id, value.channel_id, value.id, now)


async def cancel(repository, value=None, now=NOW):
    value = plan() if value is None else value
    return await repository.cancel(value.guild_id, value.actor_id, value.channel_id, value.id, now)


async def finish(repository, state, value=None):
    value = plan() if value is None else value
    return await repository.finish(value.guild_id, value.actor_id, value.channel_id, value.id, state)


@pytest.fixture(params=["sqlite", "postgres"])
async def repository(request, tmp_path, monkeypatch):
    monkeypatch.setattr(module.time, "time", lambda: NOW)
    if request.param == "sqlite":
        database = Database("sqlite+aiosqlite:///" + str(tmp_path / "Очищення з пробілами.db"))
        try:
            await database.initialize()
            yield ClearRepository(database)
        finally:
            await database.close()
        return
    configured = os.getenv("TEST_DATABASE_URL", "")
    if not configured.startswith("postgresql"):
        pytest.skip("Disposable PostgreSQL not configured")
    url = make_url(configured)
    if url.drivername != "postgresql+asyncpg" or url.database != "yeboy_test":
        raise RuntimeError("Clear SQL tests require disposable yeboy_test")
    namespace = "clear_test_" + uuid.uuid4().hex
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
        yield ClearRepository(database)
    finally:
        await database.close()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{namespace}" CASCADE'))
        await admin.dispose()


async def test_prepared_receipt_is_committed_and_bounded_in_existing_columns(repository):
    value = plan()
    record = await repository.prepare(value)
    assert record == {"plan": value, "state": "clear_confirm"}
    async with repository.database.engine.connect() as connection:
        row = (await connection.execute(select(jobs))).mappings().one()
    assert len(row["body"]) <= 1000 and row["state"] == "clear_confirm"
    assert row["channel_id"] == CHANNEL and row["user_id"] == ACTOR
    assert row["due"] == value.expires and row["lease"] == row["attempts"] == 0
    assert await get(repository) == record


async def test_same_interaction_returns_first_plan_without_changing_selection_or_expiry(repository):
    value = plan()
    original = await repository.prepare(value)
    second = replace(value, message_ids=(798,), skipped_pinned=0, expires=NOW + 40)
    assert await repository.prepare(second) == original
    assert await get(repository) == original


@pytest.mark.parametrize("change", [{"actor_id": 221}, {"channel_id": 331},
                                   {"reason": "Інша причина"}, {"requested": 4}])
async def test_same_interaction_cannot_overwrite_its_author_channel_or_payload(repository, change):
    original = await repository.prepare(plan())
    with pytest.raises(ValueError):
        await repository.prepare(replace(plan(), **change))
    assert await get(repository) == original


async def test_concurrent_prepare_keeps_one_canonical_plan(repository):
    values = [replace(plan(), message_ids=(790 - number,), skipped_pinned=0) for number in range(8)]
    records = await asyncio.gather(*(repository.prepare(value) for value in values))
    assert all(record == records[0] for record in records)
    async with repository.database.engine.connect() as connection:
        assert len((await connection.execute(select(jobs))).all()) == 1


async def test_twenty_concurrent_confirmations_have_only_one_owner(repository):
    await repository.prepare(plan())
    records = await asyncio.gather(*(claim(repository) for _ in range(20)))
    assert sum(owner for _, owner in records) == 1
    assert all(record["state"] == "clear_running" for record, _ in records)
    async with repository.database.engine.connect() as connection:
        row = (await connection.execute(select(jobs))).mappings().one()
    assert row["attempts"] == 1 and row["lease"] == 0


@pytest.mark.parametrize("terminal", ["clear_done", "clear_pending", "clear_failed"])
async def test_terminal_and_running_receipts_are_not_reclaimed_after_restart(repository, terminal):
    await repository.prepare(plan())
    assert (await claim(repository))[1]
    assert await finish(repository, terminal) == 1
    reopened = ClearRepository(repository.database)
    record, owner = await claim(reopened)
    assert not owner and record["state"] == terminal
    assert await finish(reopened, "clear_done") == 0


async def test_interrupted_running_receipt_remains_unknown_without_automatic_resume(repository):
    await repository.prepare(plan())
    assert (await claim(repository))[1]
    reopened = ClearRepository(repository.database)
    assert await claim(reopened) == ({"plan": plan(), "state": "clear_running"}, False)
    assert await repository.database.claim_jobs([GUILD], NOW + 1000) == []


@pytest.mark.parametrize("field,value", [("guild_id", 111), ("actor_id", 221), ("channel_id", 331)])
async def test_foreign_scope_cannot_read_claim_cancel_or_finish_request(repository, field, value):
    original = await repository.prepare(plan())
    scope = dict(guild_id=GUILD, actor_id=ACTOR, channel_id=CHANNEL, request_id=plan().id)
    scope[field] = value
    assert await repository.get(**scope) is None
    assert await repository.claim(**scope, now=NOW) == (None, False)
    assert await repository.cancel(**scope, now=NOW) is None
    assert await repository.finish(**scope, state="clear_done") == 0
    assert await get(repository) == original


async def test_confirm_cancel_race_has_at_most_one_execution_owner(repository):
    await repository.prepare(plan())
    (record, owner), cancellation = await asyncio.gather(claim(repository), cancel(repository))
    actual = await get(repository)
    if owner:
        assert record["state"] == actual["state"] == cancellation["state"] == "clear_running"
    else:
        assert actual["state"] == "clear_cancelled"
    assert (await claim(repository))[1] is False


@pytest.mark.parametrize("offset", [60, 61, 1000])
async def test_expired_confirmation_does_not_become_a_delete_owner(repository, offset):
    await repository.prepare(plan())
    record, owner = await claim(repository, now=NOW + offset)
    assert owner is False and record["state"] == "clear_expired"
    assert (await cancel(repository, now=NOW + offset))["state"] == "clear_expired"
    assert await finish(repository, "clear_done") == 0


async def test_expired_cancel_is_recorded_as_expired_not_cancelled(repository):
    await repository.prepare(plan())
    assert (await cancel(repository, now=NOW + 60))["state"] == "clear_expired"


async def test_duplicate_prepare_cannot_extend_an_expired_confirmation(repository, monkeypatch):
    value = plan()
    await repository.prepare(value)
    monkeypatch.setattr(module.time, "time", lambda: NOW + 61)
    original = await repository.prepare(value)
    assert original == {"plan": value, "state": "clear_expired"}
    fresh_expiry = replace(value, expires=NOW + 121, message_ids=(798,), skipped_pinned=0)
    assert await repository.prepare(fresh_expiry) == original


async def test_cap_is_atomic_and_applies_only_to_current_guild_actor_live_confirmations(repository):
    values = [replace(plan(), id=request_key(GUILD, number), request_id=number)
              for number in range(1000, 1010)]
    results = await asyncio.gather(*(repository.prepare(value) for value in values), return_exceptions=True)
    assert sum(isinstance(value, dict) for value in results) == 5
    assert all(isinstance(value, (dict, ValueError)) for value in results)
    other_actor = replace(plan(), actor_id=ACTOR + 1)
    assert (await repository.prepare(other_actor))["state"] == "clear_confirm"
    other_guild = replace(plan(), guild_id=GUILD + 1, id=request_key(GUILD + 1, REQUEST))
    assert (await repository.prepare(other_guild))["state"] == "clear_confirm"


async def test_new_prepare_expires_old_confirmations_and_reuses_available_capacity(repository, monkeypatch):
    for number in range(1000, 1005):
        await repository.prepare(replace(plan(), id=request_key(GUILD, number), request_id=number))
    monkeypatch.setattr(module.time, "time", lambda: NOW + 61)
    value = replace(plan(), expires=NOW + 121)
    assert (await repository.prepare(value))["state"] == "clear_confirm"
    async with repository.database.engine.connect() as connection:
        states = (await connection.execute(select(jobs.c.state))).scalars().all()
    assert states.count("clear_expired") == 5 and states.count("clear_confirm") == 1


async def test_reminder_read_claim_and_cancel_never_touch_clear_jobs(repository):
    database = repository.database
    reminder_id = await database.schedule(GUILD, ACTOR, CHANNEL, "Звичайне нагадування", NOW - 1)
    original = await repository.prepare(plan())
    assert [row["id"] for row in await database.reminders(GUILD, ACTOR)] == [reminder_id]
    assert await database.cancel_reminder(GUILD, ACTOR, plan().id) == 0
    claimed = await database.claim_jobs([GUILD], NOW + 1000)
    assert [row["id"] for row in claimed] == [reminder_id]
    assert await get(repository) == original
    assert await repository.get(GUILD, ACTOR, CHANNEL, reminder_id) is None


async def test_existing_nonclear_row_cannot_be_reinterpreted_or_overwritten(repository):
    value = plan()
    async with repository.database.engine.begin() as connection:
        await connection.execute(jobs.insert().values(id=value.id, guild_id=GUILD,
            user_id=ACTOR, channel_id=CHANNEL, body="Приватне нагадування", due=NOW + 60,
            state="pending", lease=0, attempts=0))
    with pytest.raises(ValueError):
        await repository.prepare(value)
    assert await get(repository) is None
    async with repository.database.engine.connect() as connection:
        row = (await connection.execute(select(jobs))).mappings().one()
    assert row["body"] == "Приватне нагадування" and row["state"] == "pending"


@pytest.mark.parametrize("payload", ["not JSON", "[2]", "[true,\"reason\",[800],1,[0,0,0]]", "x" * 1001],
                         ids=["malformed-json", "unknown-version", "boolean-version", "oversize-body"])
async def test_corrupted_payload_is_refused_before_confirmation_state_changes(repository, payload):
    await repository.prepare(plan())
    if len(payload) > 1000 and not repository.database.sqlite:
        # PostgreSQL enforces varchar(1000), whereas SQLite needs the decoder.
        # asyncpg's SQLAlchemy adapter exposes this as DBAPIError, not the
        # narrower DataError. Require the actual truncation SQLSTATE so an
        # unrelated driver/connection error cannot satisfy this assertion.
        with pytest.raises(DBAPIError) as rejected:
            async with repository.database.engine.begin() as connection:
                await connection.execute(update(jobs).where(jobs.c.id == plan().id).values(body=payload))
        assert rejected.value.orig.sqlstate == "22001"
        assert await get(repository) == {"plan": plan(), "state": "clear_confirm"}
        return
    async with repository.database.engine.begin() as connection:
        await connection.execute(update(jobs).where(jobs.c.id == plan().id).values(body=payload))
    with pytest.raises(ValueError, match="Unsupported stored"):
        await claim(repository)
    async with repository.database.engine.connect() as connection:
        assert (await connection.execute(select(jobs.c.state))).scalar_one() == "clear_confirm"


@pytest.mark.parametrize("state", [None, [], {}, "pending", "done", "clear_confirm", "clear_cancelled"])
async def test_invalid_finish_state_rejected_without_opening_sql(state):
    database = SimpleNamespace(engine=SimpleNamespace(begin=lambda: pytest.fail("Invalid finish opened SQL")))
    repository = ClearRepository(database)
    with pytest.raises(ValueError):
        await repository.finish(GUILD, ACTOR, CHANNEL, plan().id, state)


@pytest.mark.parametrize("method", ["get", "claim", "cancel", "finish"])
@pytest.mark.parametrize("change", [{"guild_id": True}, {"actor_id": 0}, {"channel_id": 2**63},
                                   {"request_id": "A" * 32}, {"request_id": None}])
async def test_invalid_scope_is_rejected_before_sql(method, change):
    database = SimpleNamespace(engine=SimpleNamespace(
        begin=lambda: pytest.fail("Invalid scope opened SQL"),
        connect=lambda: pytest.fail("Invalid scope opened SQL")))
    repository = ClearRepository(database)
    arguments = dict(guild_id=GUILD, actor_id=ACTOR, channel_id=CHANNEL, request_id=plan().id)
    arguments.update(change)
    if method in {"claim", "cancel"}:
        arguments["now"] = NOW
    if method == "finish":
        arguments["state"] = "clear_done"
    with pytest.raises(ValueError):
        await getattr(repository, method)(**arguments)


@pytest.mark.parametrize("value", [None, True, -1, float("nan"), float("inf")])
@pytest.mark.parametrize("method", ["claim", "cancel"])
async def test_invalid_clock_is_refused_before_transaction(method, value):
    database = SimpleNamespace(engine=SimpleNamespace(begin=lambda: pytest.fail("Invalid clock opened SQL")))
    repository = ClearRepository(database)
    with pytest.raises(ValueError):
        await getattr(repository, method)(GUILD, ACTOR, CHANNEL, plan().id, value)


async def _operation_waiting_past_expiry(repository, monkeypatch, operation):
    """Hold a real DB lock; advance a fake wall clock only after SQL attempts it."""
    value = plan()
    if operation != "prepare":
        await repository.prepare(value)
    engine = repository.database.engine
    attempted = asyncio.Event()

    def observe(connection, cursor, statement, parameters, context, executemany):
        sql = statement.strip().upper()
        if repository.database.sqlite:
            waiting = sql == "BEGIN IMMEDIATE"
        elif operation == "prepare":
            waiting = "PG_ADVISORY_XACT_LOCK" in sql
        else:
            waiting = "SELECT" in sql and "V2_JOBS" in sql and "FOR UPDATE" in sql
        if waiting:
            attempted.set()

    held = await engine.connect()
    transaction = await held.begin()
    task = None
    listening = False
    try:
        if repository.database.sqlite:
            await held.execute(text("BEGIN IMMEDIATE"))
        elif operation == "prepare":
            await held.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                               {"key": (GUILD ^ ACTOR) & ((1 << 63) - 1)})
        else:
            await held.execute(select(jobs.c.id).where(jobs.c.id == value.id).with_for_update())
        event.listen(engine.sync_engine, "before_cursor_execute", observe)
        listening = True
        if operation == "prepare":
            task = asyncio.create_task(repository.prepare(value))
        else:
            task = asyncio.create_task(getattr(repository, operation)(
                GUILD, ACTOR, CHANNEL, value.id, NOW))
        await asyncio.wait_for(attempted.wait(), timeout=5)
        assert not task.done()  # The request cannot yet own the held SQL lock.
        monkeypatch.setattr(module.time, "time", lambda: NOW + 61)
        await transaction.rollback()
        if operation == "prepare":
            with pytest.raises(ValueError, match="lifetime"):
                await asyncio.wait_for(task, timeout=5)
            return None
        return await asyncio.wait_for(task, timeout=5)
    finally:
        if listening:
            event.remove(engine.sync_engine, "before_cursor_execute", observe)
        if transaction.is_active:
            await transaction.rollback()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await held.close()


@pytest.mark.parametrize("operation", ["claim", "cancel"])
async def test_expiry_is_refreshed_after_waiting_for_the_real_database_lock(repository, monkeypatch, operation):
    outcome = await _operation_waiting_past_expiry(repository, monkeypatch, operation)
    record, owner = outcome if operation == "claim" else (outcome, False)
    assert owner is False and record["state"] == "clear_expired"
    assert (await get(repository))["state"] == "clear_expired"
    async with repository.database.engine.connect() as connection:
        assert (await connection.execute(select(jobs.c.attempts))).scalar_one() == 0


async def test_prepare_cannot_persist_a_plan_that_expired_while_waiting_for_the_lock(repository, monkeypatch):
    await _operation_waiting_past_expiry(repository, monkeypatch, "prepare")
    async with repository.database.engine.connect() as connection:
        assert (await connection.execute(select(jobs.c.id))).scalars().all() == []
