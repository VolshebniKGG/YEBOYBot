"""Local warning authorization, immutable receipts and uncertain SQL outcomes."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core_v2 import moderation as module
from core_v2.db import WarningConflict
from core_v2.moderation import (ADMIN, BAN, KICK, MODERATE, Context, MemberState,
                                ModerationError, ModerationService)

GUILD, ACTOR, BOT, TARGET, OWNER, REQUEST = 100, 200, 300, 400, 900, 1000
REASON = "Обґрунтоване попередження"


def member(identifier, rank=10, permissions=0, guild=GUILD, role_id=None):
    return MemberState(identifier, guild, (1, rank, -(role_id or identifier + 10000)), permissions)


def context():
    return Context(GUILD, OWNER, member(ACTOR, 20, MODERATE),
                   member(BOT, 30, MODERATE), member(TARGET))


@pytest.fixture
def invocation():
    db = SimpleNamespace(record_warning=AsyncMock(return_value=({"status": "applied"}, True)),
                         claim_case=AsyncMock(), finish_case=AsyncMock())
    gateway = SimpleNamespace(load=AsyncMock(return_value=context()), load_ban=AsyncMock(),
                              kick=AsyncMock(), ban=AsyncMock(), unban=AsyncMock(),
                              timeout=AsyncMock(), send=AsyncMock())
    state = SimpleNamespace(db=db, gateway=gateway, service=ModerationService(db, gateway))
    yield state
    # A warning is a local SQL record; no Discord mutation or two-stage pending
    # case is part of this use case, even when authorization/storage fails.
    for name in ("load_ban", "kick", "ban", "unban", "timeout", "send"):
        getattr(gateway, name).assert_not_awaited()
    db.claim_case.assert_not_awaited()
    db.finish_case.assert_not_awaited()


async def invoke(state, **changes):
    values = dict(guild_id=GUILD, actor_id=ACTOR, target_id=TARGET,
                  request_id=REQUEST, reason=REASON)
    values.update(changes)
    return await state.service.warn(**values)


async def test_fresh_complete_authorization_precedes_one_atomic_record(invocation):
    state = invocation

    async def save(*arguments):
        state.gateway.load.assert_awaited_once_with(GUILD, ACTOR, TARGET)
        assert arguments == (GUILD, f"{GUILD:016x}{REQUEST:016x}", TARGET, ACTOR, REASON)
        return {"status": "applied"}, True

    state.db.record_warning.side_effect = save
    result = await invoke(state, reason="  " + REASON + "  ")
    assert result.case_id == f"{GUILD:016x}{REQUEST:016x}"
    assert result.code == result.status == "applied"
    assert "збережено" in result.message
    assert "Discord підтвердив" not in result.message
    state.db.record_warning.assert_awaited_once()


@pytest.mark.parametrize("status", ["applied", "revoked", "pending", "failed", "historical"])
async def test_duplicate_preserves_stored_state_without_claiming_a_new_warning(invocation, status):
    state = invocation
    state.db.record_warning.return_value = {"status": status}, False
    result = await invoke(state)
    assert result.code == "duplicate" and result.status == status
    assert "Нове попередження не додано" in result.message
    state.gateway.load.assert_awaited_once_with(GUILD, ACTOR, TARGET)
    state.db.record_warning.assert_awaited_once()


async def test_repeat_is_authorized_again_before_reading_its_receipt(invocation):
    state = invocation
    assert (await invoke(state)).code == "applied"
    state.gateway.load.return_value = replace(context(), actor=member(ACTOR, 20, 0))
    with pytest.raises(ModerationError, match="Вам потрібен"):
        await invoke(state)
    assert state.gateway.load.await_count == 2
    state.db.record_warning.assert_awaited_once()


@pytest.mark.parametrize("field", ["guild_id", "actor_id", "target_id", "request_id"])
@pytest.mark.parametrize("value", [True, 0, -1, 2**63, "123", 1.0],
                         ids=["boolean", "zero", "negative", "overflow", "string", "float"])
async def test_invalid_identifiers_fail_before_discord_or_sql(invocation, field, value):
    with pytest.raises(ModerationError):
        await invoke(invocation, **{field: value})
    invocation.gateway.load.assert_not_awaited()
    invocation.db.record_warning.assert_not_awaited()


@pytest.mark.parametrize("reason", [None, 5, "", "   ", "x" * 501, "a\0b", "a\nb",
                                    "a\rb", "a\tb", "a\x7fb", "a\x80b", "a\x9fb",
                                    "a\ud800b", "a\udfffb"],
                         ids=["none", "number", "empty", "blank", "overlong", "nul", "newline",
                              "return", "tab", "delete", "c1-first", "c1-last", "surrogate-high",
                              "surrogate-low"])
async def test_invalid_reasons_fail_before_discord_or_sql(invocation, reason):
    with pytest.raises(ModerationError, match="причину"):
        await invoke(invocation, reason=reason)
    invocation.gateway.load.assert_not_awaited()
    invocation.db.record_warning.assert_not_awaited()


async def test_maximum_unicode_reason_is_preserved_without_silent_truncation(invocation):
    reason = "😀" * 500
    await invoke(invocation, reason=reason)
    assert invocation.db.record_warning.await_args.args[-1] == reason


@pytest.mark.parametrize("who,bits", [("actor", 0), ("bot", 0),
                                     ("actor", KICK | BAN), ("bot", KICK | BAN)])
async def test_current_moderate_members_required_for_author_and_bot(invocation, who, bits):
    current = context()
    current = replace(current, **{who: replace(getattr(current, who), permissions=bits)})
    invocation.gateway.load.return_value = current
    with pytest.raises(ModerationError, match="потрібен"):
        await invoke(invocation)
    invocation.db.record_warning.assert_not_awaited()


@pytest.mark.parametrize("who", ["actor", "bot"])
async def test_administrator_grants_permission_but_retains_role_checks(invocation, who):
    current = context()
    invocation.gateway.load.return_value = replace(
        current, **{who: replace(getattr(current, who), permissions=ADMIN)})
    assert (await invoke(invocation)).code == "applied"
    current = replace(invocation.gateway.load.return_value,
                      **{who: replace(getattr(current, who), permissions=ADMIN, rank=(0, 0, 0))})
    invocation.gateway.load.return_value = current
    with pytest.raises(ModerationError, match="Роль"):
        await invoke(invocation, request_id=REQUEST + 1)
    invocation.db.record_warning.assert_awaited_once()


@pytest.mark.parametrize("who", ["actor", "bot"])
@pytest.mark.parametrize("rank", [9, 10], ids=["below", "same-role"])
async def test_role_hierarchy_blocks_equal_or_lower_moderators(invocation, who, rank):
    current = context()
    adjusted = replace(getattr(current, who), rank=current.target.rank if rank == 10 else (1, 9, -999))
    invocation.gateway.load.return_value = replace(current, **{who: adjusted})
    with pytest.raises(ModerationError, match="Роль"):
        await invoke(invocation)
    invocation.db.record_warning.assert_not_awaited()


@pytest.mark.parametrize("who", ["actor", "bot"])
@pytest.mark.parametrize("role_id,allowed", [(900, True), (1100, False)],
                         ids=["lower-id-outranks", "higher-id-loses"])
async def test_same_position_role_tie_uses_discord_id_order(invocation, who, role_id, allowed):
    current = replace(context(), target=member(TARGET, 10, role_id=1000))
    invocation.gateway.load.return_value = replace(
        current, **{who: member(getattr(current, who).id, 10, MODERATE, role_id=role_id)})
    if allowed:
        assert (await invoke(invocation)).code == "applied"
    else:
        with pytest.raises(ModerationError, match="Роль"):
            await invoke(invocation)
        invocation.db.record_warning.assert_not_awaited()


async def test_owner_bypasses_actor_permission_and_rank_but_not_bot_rank(invocation):
    current = replace(context(), owner_id=ACTOR, actor=member(ACTOR, 1, 0))
    invocation.gateway.load.return_value = current
    assert (await invoke(invocation)).code == "applied"
    invocation.gateway.load.return_value = replace(current, bot=member(BOT, 1, MODERATE))
    with pytest.raises(ModerationError, match="Роль бота"):
        await invoke(invocation, request_id=REQUEST + 1)
    invocation.db.record_warning.assert_awaited_once()


async def test_warning_is_local_and_can_record_lower_ranked_administrator(invocation):
    current = context()
    invocation.gateway.load.return_value = replace(current, target=replace(current.target, permissions=ADMIN))
    assert (await invoke(invocation)).code == "applied"


@pytest.mark.parametrize("target", [ACTOR, BOT, OWNER], ids=["self", "this-bot", "owner"])
async def test_protected_targets_are_refused_before_sql(invocation, target):
    current = context()
    invocation.gateway.load.return_value = replace(current, target=member(target))
    with pytest.raises(ModerationError, match="Не можна"):
        await invoke(invocation, target_id=target)
    invocation.db.record_warning.assert_not_awaited()


@pytest.mark.parametrize("mutation", ["guild", "actor-id", "actor-guild", "bot-guild", "bot-id",
                                      "target-id", "target-guild", "target-absent", "owner-id"])
async def test_inconsistent_fresh_context_cannot_write_warning(invocation, mutation):
    current = context()
    if mutation == "guild":
        current = replace(current, guild_id=101)
    elif mutation == "actor-id":
        current = replace(current, actor=replace(current.actor, id=201))
    elif mutation == "actor-guild":
        current = replace(current, actor=replace(current.actor, guild_id=101))
    elif mutation == "bot-guild":
        current = replace(current, bot=replace(current.bot, guild_id=101))
    elif mutation == "bot-id":
        current = replace(current, bot=replace(current.bot, id=0))
    elif mutation == "target-id":
        current = replace(current, target=replace(current.target, id=401))
    elif mutation == "target-guild":
        current = replace(current, target=replace(current.target, guild_id=101))
    elif mutation == "target-absent":
        current = replace(current, target=None)
    else:
        current = replace(current, owner_id=0)
    invocation.gateway.load.return_value = current
    with pytest.raises(ModerationError):
        await invoke(invocation)
    invocation.db.record_warning.assert_not_awaited()


@pytest.mark.parametrize("error", [ModerationError("Актуальні права не підтверджено"),
                                 TimeoutError("private transport detail"), RuntimeError("private response")],
                         ids=["controlled", "timeout", "unexpected"])
async def test_discord_read_failure_cannot_leave_a_pending_warning(invocation, error):
    invocation.gateway.load.side_effect = error
    with pytest.raises(ModerationError) as rejected:
        await invoke(invocation)
    assert "private" not in str(rejected.value)
    invocation.db.record_warning.assert_not_awaited()


async def test_discord_read_timeout_is_bounded_and_precedes_sql(invocation, monkeypatch):
    entered = asyncio.Event()

    async def hang(*arguments):
        entered.set()
        await asyncio.Event().wait()

    invocation.gateway.load.side_effect = hang
    real_timeout = asyncio.timeout
    durations = []

    def bounded(seconds):
        durations.append(seconds)
        return real_timeout(0 if seconds == 15 else seconds)

    monkeypatch.setattr(module.asyncio, "timeout", bounded)
    with pytest.raises(ModerationError):
        await invoke(invocation)
    assert entered.is_set() and durations == [15]
    invocation.db.record_warning.assert_not_awaited()


@pytest.mark.parametrize("error", [RuntimeError("private db data"), ConnectionResetError("private credentials"),
                                 TimeoutError("private COMMIT uncertainty"), ValueError("private decoding detail")],
                         ids=["database", "connection", "timeout", "postcommit-valueerror"])
async def test_storage_failure_does_not_claim_that_warning_was_not_created(invocation, error):
    invocation.db.record_warning.side_effect = error
    result = await invoke(invocation)
    assert result.code == "unconfirmed" and result.status is None
    assert "не підтверджено" in result.message and "/modhistory" in result.message
    assert "не додано" not in result.message and "private" not in result.message
    invocation.db.record_warning.assert_awaited_once()


async def test_storage_timeout_is_bounded_with_unknown_commit_outcome(invocation, monkeypatch):
    entered = asyncio.Event()

    async def hang(*arguments):
        entered.set()
        await asyncio.Event().wait()

    invocation.db.record_warning.side_effect = hang
    real_timeout = asyncio.timeout
    durations = []

    def bounded(seconds):
        durations.append(seconds)
        return real_timeout(0 if seconds == 10 else seconds)

    monkeypatch.setattr(module.asyncio, "timeout", bounded)
    result = await invoke(invocation)
    assert entered.is_set() and durations == [15, 10]
    assert result.code == "unconfirmed" and result.status is None
    invocation.db.record_warning.assert_awaited_once()


async def test_payload_conflict_is_controlled_without_exposing_original_data(invocation, caplog):
    invocation.db.record_warning.side_effect = WarningConflict("private original reason")
    with pytest.raises(ModerationError, match="іншими параметрами") as rejected:
        await invoke(invocation)
    assert "private" not in str(rejected.value) + caplog.text
    invocation.db.record_warning.assert_awaited_once()


async def test_raw_database_error_and_reason_are_not_logged(invocation, caplog):
    private_reason = "Приватна причина для перевірки"
    invocation.db.record_warning.side_effect = RuntimeError("https://secret:password@example.invalid/db")
    result = await invoke(invocation, reason=private_reason)
    assert result.code == "unconfirmed"
    assert "RuntimeError" in caplog.text
    for private in ("secret", "password", "example.invalid", private_reason):
        assert private not in caplog.text + result.message


@pytest.mark.parametrize("where", ["discord", "database"])
async def test_cancellation_is_propagated_without_fabricating_a_result(invocation, where):
    boundary = invocation.gateway.load if where == "discord" else invocation.db.record_warning
    boundary.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await invoke(invocation)
    if where == "discord":
        invocation.db.record_warning.assert_not_awaited()
    else:
        invocation.db.record_warning.assert_awaited_once()
