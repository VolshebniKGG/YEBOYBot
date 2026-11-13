"""SQLite → clear service → fresh SDK models → offline HTTP, including failures."""
import asyncio
from types import SimpleNamespace

import pytest

from core_v2 import clear_repository as repository_module
from core_v2.clear_repository import ClearRepository
from core_v2.clear_service import ClearService
from core_v2.db import Database
from core_v2.moderation import ModerationError
from test_clear_gateway import (
    ACTOR, ACTOR_ROLE, CHANNEL, FIRST, GUILD, NOW, REQUEST, SECOND,
    api_error, channel_state as channel_state,
)
from test_discord_moderation import member_payload

REASON = "Прибрати підтверджений спам"


@pytest.fixture
async def integrated(channel_state, tmp_path, monkeypatch):
    database = Database("sqlite+aiosqlite:///" + str(tmp_path / "Очищення з пробілами.db"))
    try:
        await database.initialize()
        # Repository lifetime checks and the service use one explicit clock.
        # monkeypatch restores the shared time module after each fixture.
        monkeypatch.setattr(repository_module.time, "time", lambda: NOW.timestamp())
        repository = ClearRepository(database)
        yield SimpleNamespace(database=database, repository=repository,
                              service=ClearService(repository, channel_state.gateway,
                                                   clock=lambda: NOW.timestamp()), state=channel_state)
    finally:
        await database.close()


async def preview(fixture, amount=2):
    if amount == 1:
        fixture.state.logs.return_value = [fixture.state.messages[FIRST]]
    return await fixture.service.prepare(GUILD, ACTOR, CHANNEL, REQUEST, amount, REASON)


@pytest.mark.parametrize("amount", [1, 2])
async def test_confirmation_commits_before_sdk_write_and_receipt_prevents_reselection(integrated, amount):
    fixture, state = integrated, integrated.state
    record = await preview(fixture, amount)
    plan = record["plan"]
    assert record["state"] == "clear_confirm"
    assert plan.message_ids == ((FIRST,) if amount == 1 else (FIRST, SECOND))
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()

    async def mutate(*arguments, reason):
        saved = await fixture.repository.get(GUILD, ACTOR, CHANNEL, plan.id)
        assert saved["state"] == "clear_running"
        assert saved["plan"].message_ids == plan.message_ids
        assert saved["plan"].reason == REASON
        # Clear states remain isolated from background reminder delivery.
        assert await fixture.database.claim_jobs([GUILD], NOW.timestamp() + 1000) == []
        assert f"clear={plan.id}" in reason and f"actor={ACTOR}" in reason
        assert f"channel={CHANNEL}" in reason
        assert arguments == ((CHANNEL, FIRST) if amount == 1 else (CHANNEL, [FIRST, SECOND]))

    handler = state.single if amount == 1 else state.bulk
    handler.side_effect = mutate
    result = await fixture.service.execute(GUILD, ACTOR, CHANNEL, plan.id)
    assert result.code == "confirmed" and result.count == amount
    assert (await fixture.service.status(GUILD, ACTOR, CHANNEL, plan.id))["state"] == "clear_done"
    assert (await fixture.service.execute(GUILD, ACTOR, CHANNEL, plan.id)).code == "duplicate"
    handler.assert_awaited_once()
    state.logs.assert_awaited_once_with(CHANNEL, amount, before=REQUEST)
    assert {call.args for call in state.get_message.await_args_list} == {
        (CHANNEL, identifier) for identifier in plan.message_ids}


@pytest.mark.parametrize("changed", ["actor", "guild", "channel"])
async def test_foreign_confirmation_scope_cannot_consume_saved_request(integrated, changed):
    fixture, state = integrated, integrated.state
    record = await preview(fixture)
    plan = record["plan"]
    guild_id, actor_id, channel_id = GUILD, ACTOR, CHANNEL
    if changed == "actor":
        actor_id += 1
        state.members[actor_id] = member_payload(actor_id, [ACTOR_ROLE])
    elif changed == "guild":
        guild_id += 1
    else:
        channel_id += 1
        state.channel["id"] = str(channel_id)
    with pytest.raises(ModerationError):
        await fixture.service.execute(guild_id, actor_id, channel_id, plan.id)
    assert (await fixture.repository.get(GUILD, ACTOR, CHANNEL, plan.id))["state"] == "clear_confirm"
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()


async def test_sdk_write_404_keeps_durable_unknown_receipt_after_service_restart(integrated):
    fixture, state = integrated, integrated.state
    plan = (await preview(fixture))["plan"]
    state.bulk.side_effect = api_error()
    result = await fixture.service.execute(GUILD, ACTOR, CHANNEL, plan.id)
    assert result.code == "pending" and result.state == "clear_pending"
    restarted = ClearService(ClearRepository(fixture.database), state.gateway, clock=lambda: NOW.timestamp())
    assert (await restarted.status(GUILD, ACTOR, CHANNEL, plan.id))["state"] == "clear_pending"
    assert (await restarted.execute(GUILD, ACTOR, CHANNEL, plan.id)).code == "duplicate"
    state.bulk.assert_awaited_once()
    state.logs.assert_awaited_once()


async def test_message_pinned_after_preview_blocks_entire_request_before_write(integrated):
    fixture, state = integrated, integrated.state
    plan = (await preview(fixture))["plan"]
    state.messages[FIRST]["pinned"] = True
    result = await fixture.service.execute(GUILD, ACTOR, CHANNEL, plan.id)
    assert result.code == "not_sent" and result.state == "clear_failed"
    assert (await fixture.repository.get(GUILD, ACTOR, CHANNEL, plan.id))["state"] == "clear_failed"
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()
    state.logs.assert_awaited_once()


async def test_permission_loss_after_preview_refuses_confirmation_without_consuming_it(integrated):
    fixture, state = integrated, integrated.state
    plan = (await preview(fixture))["plan"]
    state.fresh["roles"][1]["permissions"] = "0"
    with pytest.raises(ModerationError):
        await fixture.service.execute(GUILD, ACTOR, CHANNEL, plan.id)
    assert (await fixture.repository.get(GUILD, ACTOR, CHANNEL, plan.id))["state"] == "clear_confirm"
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()


async def test_cancellation_after_sdk_dispatch_cannot_be_replayed(integrated):
    fixture, state = integrated, integrated.state
    plan = (await preview(fixture))["plan"]
    dispatched = asyncio.Event()
    async def waiting(*args, **kwargs):
        dispatched.set()
        await asyncio.Future()
    state.bulk.side_effect = waiting
    operation = asyncio.create_task(fixture.service.execute(GUILD, ACTOR, CHANNEL, plan.id))
    try:
        await asyncio.wait_for(dispatched.wait(), timeout=3)
        assert (await fixture.repository.get(GUILD, ACTOR, CHANNEL, plan.id))["state"] == "clear_running"
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        restarted = ClearService(ClearRepository(fixture.database), state.gateway, clock=lambda: NOW.timestamp())
        assert (await restarted.execute(GUILD, ACTOR, CHANNEL, plan.id)).code == "duplicate"
        assert (await restarted.status(GUILD, ACTOR, CHANNEL, plan.id))["state"] == "clear_running"
        state.bulk.assert_awaited_once()
    finally:
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


async def test_sdk_success_with_database_finalization_failure_is_explicit_and_not_replayed(integrated, monkeypatch):
    fixture, state = integrated, integrated.state
    plan = (await preview(fixture))["plan"]
    async def fail(*args):
        raise RuntimeError("private-clear-finalization-payload")
    monkeypatch.setattr(fixture.repository, "finish", fail)
    result = await fixture.service.execute(GUILD, ACTOR, CHANNEL, plan.id)
    assert result.code == "api_confirmed_db_unknown" and result.count == 2
    assert "private-clear-finalization-payload" not in result.message
    assert (await fixture.repository.get(GUILD, ACTOR, CHANNEL, plan.id))["state"] == "clear_running"
    assert (await fixture.service.execute(GUILD, ACTOR, CHANNEL, plan.id)).code == "duplicate"
    state.bulk.assert_awaited_once()
