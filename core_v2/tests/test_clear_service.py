"""Durable confirmation and uncertain-outcome invariants, with real SQLite."""
import asyncio
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest

from core_v2.clear_plan import request_key
from core_v2.clear_repository import ClearRepository
from core_v2.clear_service import ClearService
from core_v2.db import Database
from core_v2.moderation import ModerationError

GUILD, ACTOR, CHANNEL, REQUEST = 101, 202, 303, 9000
IDS = (8000, 7000)
REASON = "Обґрунтована причина очищення"


class Gateway:
    def __init__(self):
        self.check = AsyncMock()
        self.snapshot = AsyncMock(return_value=SimpleNamespace(
            message_ids=IDS, skipped_pinned=0, skipped_old=0, skipped_system=0))
        self.validate = AsyncMock()
        self.delete = AsyncMock()


@pytest.fixture
async def workflow(tmp_path):
    database = Database("sqlite+aiosqlite:///" + str(tmp_path / "Очищення з пробілами.db"))
    await database.initialize()
    clock = SimpleNamespace(now=time.time())
    repository, gateway = ClearRepository(database), Gateway()
    service = ClearService(repository, gateway, clock=lambda: clock.now)
    try:
        yield SimpleNamespace(database=database, repository=repository, gateway=gateway,
                              service=service, clock=clock)
    finally:
        await database.close()


async def prepare(workflow, **changes):
    arguments = dict(guild_id=GUILD, actor_id=ACTOR, channel_id=CHANNEL,
                     interaction_id=REQUEST, amount=2, reason=REASON)
    arguments.update(changes)
    return await workflow.service.prepare(**arguments)


async def execute(workflow, **changes):
    arguments = dict(guild_id=GUILD, actor_id=ACTOR, channel_id=CHANNEL,
                     request_id=request_key(GUILD, REQUEST))
    arguments.update(changes)
    return await workflow.service.execute(arguments["guild_id"], arguments["actor_id"],
                                          arguments["channel_id"], arguments["request_id"])


async def stored(workflow):
    return await workflow.repository.get(GUILD, ACTOR, CHANNEL, request_key(GUILD, REQUEST))


async def test_prepare_saves_exact_plan_and_reason_before_any_confirmation(workflow):
    state = workflow
    record = await prepare(state, reason="  " + REASON + "  ")
    plan = record["plan"]
    assert record["state"] == "clear_confirm"
    assert plan.id == request_key(GUILD, REQUEST)
    assert (plan.guild_id, plan.actor_id, plan.channel_id, plan.request_id) == (GUILD, ACTOR, CHANNEL, REQUEST)
    assert plan.reason == REASON and plan.message_ids == IDS
    assert plan.expires == state.clock.now + 60
    assert await stored(state) == record
    state.gateway.check.assert_awaited_once_with(GUILD, ACTOR, CHANNEL)
    state.gateway.snapshot.assert_awaited_once_with(GUILD, ACTOR, CHANNEL, REQUEST, 2)
    state.gateway.validate.assert_not_awaited()
    state.gateway.delete.assert_not_awaited()


async def test_preview_replay_rechecks_permissions_without_replacing_plan_or_lifetime(workflow):
    state = workflow
    first = await prepare(state)
    state.clock.now += 10
    state.gateway.snapshot.return_value = SimpleNamespace(
        message_ids=(6000,), skipped_pinned=1, skipped_old=0, skipped_system=0)
    second = await prepare(state)
    assert second == first
    assert state.gateway.check.await_count == 2
    state.gateway.snapshot.assert_awaited_once()
    state.gateway.delete.assert_not_awaited()


@pytest.mark.parametrize("change", [
    {"guild_id": 0}, {"actor_id": True}, {"channel_id": -1}, {"interaction_id": 2**63},
    {"amount": 0}, {"amount": 26}, {"amount": True}, {"reason": " "},
    {"reason": "x" * 201}, {"reason": "bad\0reason"}, {"reason": "line\nbreak"},
], ids=["guild", "actor", "channel", "interaction", "zero", "large", "boolean",
        "blank", "long", "nul", "newline"])
async def test_invalid_preview_never_queries_discord_or_creates_request(workflow, change):
    state = workflow
    with pytest.raises(ModerationError):
        await prepare(state, **change)
    state.gateway.check.assert_not_awaited()
    state.gateway.snapshot.assert_not_awaited()
    state.gateway.delete.assert_not_awaited()
    assert await stored(state) is None


async def test_empty_selection_does_not_create_confirmation_or_delete(workflow):
    state = workflow
    state.gateway.snapshot.return_value = SimpleNamespace(
        message_ids=(), skipped_pinned=1, skipped_old=1, skipped_system=0)
    with pytest.raises(ModerationError):
        await prepare(state)
    assert await stored(state) is None
    state.gateway.delete.assert_not_awaited()


async def test_authority_is_checked_even_when_preview_already_exists(workflow):
    state = workflow
    original = await prepare(state)
    state.gateway.check.side_effect = ModerationError("Доступ змінено.")
    with pytest.raises(ModerationError):
        await prepare(state)
    assert await stored(state) == original
    assert state.gateway.snapshot.await_count == 1


async def test_committed_claim_and_validation_precede_the_only_sdk_call(workflow):
    state = workflow
    record = await prepare(state)

    async def check_claim_and_message_ids(plan, audit_reason):
        assert (await stored(state))["state"] == "clear_running"
        state.gateway.validate.assert_awaited_once_with(record["plan"])
        assert plan == record["plan"] and plan.message_ids == IDS
        assert plan.id in audit_reason and str(ACTOR) in audit_reason and str(CHANNEL) in audit_reason

    state.gateway.delete.side_effect = check_claim_and_message_ids
    result = await execute(state)
    assert result.state == "clear_done" and result.count == len(IDS)
    assert (await stored(state))["state"] == "clear_done"
    assert state.gateway.check.await_count == 2
    state.gateway.delete.assert_awaited_once()


async def test_concurrent_confirmations_have_one_owner_and_never_reselect_history(workflow):
    state = workflow
    await prepare(state)
    results = await asyncio.gather(*(execute(state) for _ in range(8)))
    assert any(result.state == "clear_done" for result in results)
    state.gateway.delete.assert_awaited_once()
    state.gateway.validate.assert_awaited_once()
    state.gateway.snapshot.assert_awaited_once()
    assert (await stored(state))["state"] == "clear_done"


@pytest.mark.parametrize("prior", ["clear_running", "clear_pending", "clear_done"])
async def test_restart_never_replays_a_claimed_or_completed_request(workflow, prior):
    state = workflow
    await prepare(state)
    record, won = await state.repository.claim(GUILD, ACTOR, CHANNEL, request_key(GUILD, REQUEST), state.clock.now)
    assert won and record["state"] == "clear_running"
    if prior != "clear_running":
        assert await state.repository.finish(GUILD, ACTOR, CHANNEL, record["plan"].id, prior) == 1
    restarted_gateway = Gateway()
    restarted = ClearService(ClearRepository(state.database), restarted_gateway, clock=lambda: state.clock.now)
    result = await restarted.execute(GUILD, ACTOR, CHANNEL, record["plan"].id)
    assert result.state == prior
    restarted_gateway.delete.assert_not_awaited()
    restarted_gateway.validate.assert_not_awaited()
    restarted_gateway.snapshot.assert_not_awaited()


async def test_expiry_at_confirmation_blocks_action_and_cannot_be_extended(workflow):
    state = workflow
    first = await prepare(state)
    state.clock.now = first["plan"].expires
    result = await execute(state)
    assert result.state == "clear_expired"
    assert (await stored(state))["state"] == "clear_expired"
    state.gateway.validate.assert_not_awaited()
    state.gateway.delete.assert_not_awaited()


async def test_cancel_remains_metadata_only_and_original_request_cannot_be_confirmed(workflow):
    state = workflow
    await prepare(state)
    state.gateway.check.side_effect = ModerationError("Бот утратив доступ.")
    result = await state.service.cancel(GUILD, ACTOR, CHANNEL, request_key(GUILD, REQUEST))
    assert result.state == "clear_cancelled"
    assert state.gateway.check.await_count == 1  # only the earlier preview
    state.gateway.check.side_effect = None
    repeated = await execute(state)
    assert repeated.state == "clear_cancelled"
    state.gateway.delete.assert_not_awaited()


@pytest.mark.parametrize("change", [{"guild_id": GUILD + 1}, {"actor_id": ACTOR + 1},
                                     {"channel_id": CHANNEL + 1}], ids=["guild", "actor", "channel"])
async def test_foreign_confirmation_cannot_read_or_mutate_owners_receipt(workflow, change):
    state = workflow
    original = await prepare(state)
    with pytest.raises(ModerationError):
        await execute(state, **change)
    assert await stored(state) == original
    state.gateway.delete.assert_not_awaited()


async def test_validation_failure_is_recorded_as_prewrite_failure(workflow):
    state = workflow
    await prepare(state)
    state.gateway.validate.side_effect = ModerationError("Повідомлення закріплено після preview.")
    result = await execute(state)
    assert result.code == "not_sent" and result.state == "clear_failed"
    assert (await stored(state))["state"] == "clear_failed"
    state.gateway.delete.assert_not_awaited()


async def test_permission_loss_before_claim_does_not_take_ownership(workflow):
    state = workflow
    original = await prepare(state)
    state.gateway.check.side_effect = ModerationError("Дозволи змінилися.")
    with pytest.raises(ModerationError):
        await execute(state)
    assert await stored(state) == original
    state.gateway.validate.assert_not_awaited()
    state.gateway.delete.assert_not_awaited()


async def test_db_claim_failure_stops_before_remote_action_without_leaking_details(workflow, monkeypatch):
    state = workflow
    await prepare(state)
    monkeypatch.setattr(state.repository, "claim", AsyncMock(side_effect=RuntimeError("private-db-credentials")))
    with pytest.raises(ModerationError) as error:
        await execute(state)
    assert "private-db-credentials" not in str(error.value)
    state.gateway.delete.assert_not_awaited()


async def test_lost_claim_receipt_after_commit_cannot_dispatch_or_reopen_request(workflow, monkeypatch):
    state = workflow
    await prepare(state)
    original_claim = state.repository.claim

    async def claim_then_lose_acknowledgment(*arguments):
        await original_claim(*arguments)
        raise OSError("private-commit-receipt-error")

    monkeypatch.setattr(state.repository, "claim", claim_then_lose_acknowledgment)
    with pytest.raises(ModerationError) as error:
        await execute(state)
    assert "private-commit-receipt-error" not in str(error.value)
    assert (await stored(state))["state"] == "clear_running"
    state.gateway.delete.assert_not_awaited()
    monkeypatch.setattr(state.repository, "claim", original_claim)
    repeated = await execute(state)
    assert repeated.state == "clear_running"
    state.gateway.delete.assert_not_awaited()


async def test_lost_preview_commit_receipt_is_recovered_without_reselecting_ids(workflow, monkeypatch):
    state = workflow
    original_prepare = state.repository.prepare

    async def prepare_then_lose_acknowledgment(plan):
        await original_prepare(plan)
        raise OSError("private-preview-commit-error")

    monkeypatch.setattr(state.repository, "prepare", prepare_then_lose_acknowledgment)
    with pytest.raises(ModerationError):
        await prepare(state)
    first = await stored(state)
    assert first["state"] == "clear_confirm"
    state.clock.now += 2
    recovered = await prepare(state)
    assert recovered == first
    state.gateway.snapshot.assert_awaited_once()
    state.gateway.delete.assert_not_awaited()


async def test_prewrite_failure_with_unknown_recording_remains_not_sent(workflow, monkeypatch):
    state = workflow
    await prepare(state)
    state.gateway.validate.side_effect = RuntimeError("private-validation-payload")
    monkeypatch.setattr(state.repository, "finish", AsyncMock(return_value=0))
    result = await execute(state)
    assert result.code == "not_sent_unrecorded" and result.state is None and result.count == 0
    assert "private-validation-payload" not in result.message
    assert (await stored(state))["state"] == "clear_running"
    state.gateway.delete.assert_not_awaited()


@pytest.mark.parametrize("exception", [OSError("private-network-payload"), TimeoutError("private-timeout"),
                                      ModerationError("private-final-auth-payload")],
                         ids=["network", "timeout", "final-read"])
async def test_dispatch_errors_are_uncertain_and_never_blindly_replayed(workflow, exception, caplog):
    state = workflow
    await prepare(state)
    state.gateway.delete.side_effect = exception
    result = await execute(state)
    assert result.state == "clear_pending" and result.count == 0
    assert (await stored(state))["state"] == "clear_pending"
    assert "private-" not in result.message and "private-" not in caplog.text
    repeated = await execute(state)
    assert repeated.state == "clear_pending"
    state.gateway.delete.assert_awaited_once()


@pytest.mark.parametrize("failure", ["exception", "cas-zero"])
async def test_sdk_acknowledgment_survives_unknown_database_finalization(workflow, monkeypatch, failure):
    state = workflow
    await prepare(state)
    finish = AsyncMock(side_effect=OSError("private-finalization-detail")) if failure == "exception" else AsyncMock(return_value=0)
    monkeypatch.setattr(state.repository, "finish", finish)
    result = await execute(state)
    assert result.code == "api_confirmed_db_unknown" and result.count == len(IDS)
    assert result.state != "clear_failed"
    assert "private-finalization-detail" not in result.message
    assert (await stored(state))["state"] == "clear_running"
    state.gateway.delete.assert_awaited_once()


async def test_cancellation_during_sdk_dispatch_leaves_unconfirmed_receipt_and_no_retry(workflow):
    state = workflow
    await prepare(state)
    state.gateway.delete.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await execute(state)
    assert (await stored(state))["state"] in {"clear_running", "clear_pending"}
    result = await execute(state)
    assert result.state in {"clear_running", "clear_pending"}
    state.gateway.delete.assert_awaited_once()


async def test_cancellation_before_dispatch_cannot_reopen_the_owned_request(workflow):
    state = workflow
    await prepare(state)
    state.gateway.validate.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await execute(state)
    assert (await stored(state))["state"] in {"clear_running", "clear_pending", "clear_failed"}
    state.gateway.validate.side_effect = None
    await execute(state)
    state.gateway.delete.assert_not_awaited()


async def test_status_is_scoped_and_can_show_owners_receipt_without_discord_permission(workflow):
    state = workflow
    record = await prepare(state)
    state.gateway.check.reset_mock()
    state.gateway.check.side_effect = AssertionError("Status must not require Discord mutation rights")
    assert await state.service.status(GUILD, ACTOR, CHANNEL, record["plan"].id) == record
    state.gateway.check.assert_not_awaited()
    with pytest.raises(ModerationError):
        await state.service.status(GUILD, ACTOR + 1, CHANNEL, record["plan"].id)
    state.gateway.delete.assert_not_awaited()


async def test_expired_status_is_effective_read_only_state(workflow):
    state = workflow
    original = await prepare(state)
    state.clock.now = original["plan"].expires + 1
    result = await state.service.status(GUILD, ACTOR, CHANNEL, original["plan"].id)
    assert result["state"] == "clear_expired"
    assert (await stored(state))["state"] == "clear_confirm"
    state.gateway.delete.assert_not_awaited()


async def test_unicode_reason_is_kept_whole_while_audit_header_is_encoded_bounded(workflow):
    state = workflow
    reason = "Ї🚀" * 100
    record = await prepare(state, reason=reason)
    result = await execute(state)
    assert result.state == "clear_done"
    assert record["plan"].reason == reason
    assert (await stored(state))["plan"].reason == reason
    header = state.gateway.delete.await_args.args[1]
    assert record["plan"].id in header and str(ACTOR) in header and str(CHANNEL) in header
    assert len(quote(header, safe="/ ")) <= 512


async def test_single_prepared_id_is_acknowledged_without_silently_refilling_selection(workflow):
    state = workflow
    state.gateway.snapshot.return_value = SimpleNamespace(
        message_ids=(IDS[0],), skipped_pinned=1, skipped_old=0, skipped_system=0)
    original = await prepare(state)
    assert original["plan"].requested == 2
    result = await execute(state)
    assert result.count == 1 and result.state == "clear_done"
    assert state.gateway.delete.await_args.args[0].message_ids == (IDS[0],)
    state.gateway.snapshot.assert_awaited_once()


async def test_preview_capacity_has_actionable_error_without_deleting_or_losing_receipts(workflow):
    state = workflow
    originals = [await prepare(state, interaction_id=REQUEST + offset) for offset in range(5)]
    with pytest.raises(ModerationError, match="Максимум 5"):
        await prepare(state, interaction_id=REQUEST + 5)
    for record in originals:
        assert await state.service.status(GUILD, ACTOR, CHANNEL, record["plan"].id) == record
    state.gateway.delete.assert_not_awaited()
