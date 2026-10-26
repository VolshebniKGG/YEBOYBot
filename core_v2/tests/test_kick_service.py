"""Kick authorization and uncertain outcomes using real SQLite audit rows."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core_v2.db import Database
from core_v2.moderation import Context, KickService, MemberState, ModerationError

KICK = 1 << 1
BAN = 1 << 2
ADMIN = 1 << 3
MODERATE = 1 << 40
GUILD, ACTOR, BOT, TARGET, OWNER, REQUEST = 100, 200, 300, 400, 900, 1000


def member(identifier, position, permissions=0, guild=GUILD, role_id=None):
    role_id = role_id or identifier + 10000
    return MemberState(identifier, guild, (1, position, -role_id), permissions)


def context(*, actor_permissions=KICK, bot_permissions=KICK, owner=OWNER, target=True):
    return Context(GUILD, owner, member(ACTOR, 20, actor_permissions),
                   member(BOT, 30, bot_permissions), member(TARGET, 10) if target else None)


class Gateway:
    def __init__(self, initial=None, final=None):
        self.initial = initial or context(target=False)
        self.final = final or context()
        self.load = AsyncMock(side_effect=self._load)
        self.kick = AsyncMock()

    async def _load(self, guild_id, actor_id, target_id=None):
        return self.initial if target_id is None else self.final


@pytest.fixture
async def database(tmp_path):
    db = Database("sqlite+aiosqlite:///" + str(tmp_path / "kick.db"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def invoke(service, **changes):
    args = dict(guild_id=GUILD, actor_id=ACTOR, target_id=TARGET, request_id=REQUEST,
                reason="  Порушення правил  ")
    args.update(changes)
    return await service.kick(**args)


async def test_success_has_committed_pending_before_delete_and_records_applied(database):
    gateway = Gateway()
    service = KickService(database, gateway)

    async def delete(ctx, target_id, audit_reason):
        rows = await database.case_history(GUILD)
        assert len(rows) == 1 and rows[0]["status"] == "pending"
        assert rows[0]["reason"] == "Порушення правил"
        assert rows[0]["id"] in audit_reason
        assert str(ACTOR) in audit_reason
        assert len(audit_reason) <= 512
        assert target_id == TARGET

    gateway.kick.side_effect = delete
    outcome = await invoke(service)
    assert outcome.code == "applied"
    rows = await database.case_history(GUILD)
    assert rows[0]["id"] == outcome.case_id
    assert rows[0]["status"] == "applied"
    assert rows[0]["actor_id"] == ACTOR and rows[0]["user_id"] == TARGET
    assert gateway.load.await_count == 2
    gateway.kick.assert_awaited_once()


async def test_concurrent_same_interaction_executes_one_delete(database):
    gateway = Gateway()
    service = KickService(database, gateway)
    outcomes = await asyncio.gather(*(invoke(service) for _ in range(8)))
    assert [outcome.code for outcome in outcomes].count("applied") == 1
    assert len({outcome.case_id for outcome in outcomes}) == 1
    assert len(await database.case_history(GUILD)) == 1
    gateway.kick.assert_awaited_once()


async def test_repeat_after_completed_action_does_not_need_target_membership(database):
    gateway = Gateway()
    first = await invoke(KickService(database, gateway))
    gateway.load.reset_mock()
    gateway.kick.reset_mock()

    async def authority_only(guild_id, actor_id, target_id=None):
        assert target_id is None, "Duplicate request must not refetch a former member"
        return gateway.initial

    gateway.load.side_effect = authority_only
    repeated = await invoke(KickService(database, gateway))
    assert repeated.code == "duplicate"
    assert repeated.case_id == first.case_id
    assert repeated.status == "applied"
    gateway.kick.assert_not_awaited()


async def test_pending_after_restart_is_diagnosed_without_retry(database):
    gateway = Gateway()
    gateway.kick.side_effect = TimeoutError("unconfirmed remote operation")
    first = await invoke(KickService(database, gateway))
    assert first.code == "pending"
    gateway.kick.reset_mock()
    gateway.kick.side_effect = None
    repeated = await invoke(KickService(database, gateway))
    assert repeated.code == "duplicate" and repeated.status == "pending"
    gateway.kick.assert_not_awaited()
    assert (await database.case_history(GUILD))[0]["status"] == "pending"


@pytest.mark.parametrize("actor_permissions,bot_permissions", [(0, KICK), (KICK, 0)])
async def test_missing_initial_permission_writes_no_case_or_delete(database, actor_permissions, bot_permissions):
    gateway = Gateway(initial=context(actor_permissions=actor_permissions, bot_permissions=bot_permissions, target=False))
    with pytest.raises(ModerationError):
        await invoke(KickService(database, gateway))
    assert await database.case_history(GUILD) == []
    gateway.kick.assert_not_awaited()


async def test_authority_change_after_claim_blocks_delete_and_records_failed(database):
    gateway = Gateway(final=context(actor_permissions=0))
    outcome = await invoke(KickService(database, gateway))
    assert outcome.code == "not_sent"
    assert (await database.case_history(GUILD))[0]["status"] == "failed"
    gateway.kick.assert_not_awaited()


@pytest.mark.parametrize("mutation", [
    "actor_below", "actor_equal", "bot_below", "bot_equal", "target_owner",
    "target_self", "target_bot", "target_other_guild", "actor_other_guild",
    "bot_other_guild", "context_other_guild",
])
async def test_final_hierarchy_and_context_guards_prevent_delete(database, mutation):
    final = context()
    target_id = TARGET
    if mutation == "actor_below":
        final = replace(final, actor=member(ACTOR, 9, KICK))
    elif mutation == "actor_equal":
        final = replace(final, actor=replace(final.actor, rank=final.target.rank))
    elif mutation == "bot_below":
        final = replace(final, bot=member(BOT, 9, KICK))
    elif mutation == "bot_equal":
        final = replace(final, bot=replace(final.bot, rank=final.target.rank))
    elif mutation == "target_owner":
        final = replace(final, owner_id=TARGET)
    elif mutation == "target_self":
        target_id = ACTOR
        final = replace(final, target=final.actor)
    elif mutation == "target_bot":
        target_id = BOT
        final = replace(final, target=final.bot)
    elif mutation == "target_other_guild":
        final = replace(final, target=replace(final.target, guild_id=101))
    elif mutation == "actor_other_guild":
        final = replace(final, actor=replace(final.actor, guild_id=101))
    elif mutation == "bot_other_guild":
        final = replace(final, bot=replace(final.bot, guild_id=101))
    else:
        final = replace(final, guild_id=101)
    gateway = Gateway(final=final)
    try:
        outcome = await invoke(KickService(database, gateway), target_id=target_id)
        assert outcome.code == "not_sent"
    except ModerationError:
        # Self/bot mismatches may be rejected before a case claim as well.
        assert mutation in {"target_self", "target_bot"}
    gateway.kick.assert_not_awaited()


async def test_owner_bypasses_actor_hierarchy_but_not_bot_hierarchy(database):
    initial = replace(context(target=False), owner_id=ACTOR, actor=member(ACTOR, 1, 0))
    final = replace(context(), owner_id=ACTOR, actor=member(ACTOR, 1, 0))
    gateway = Gateway(initial, final)
    assert (await invoke(KickService(database, gateway))).code == "applied"
    final = replace(final, bot=member(BOT, 1, KICK))
    gateway = Gateway(initial, final)
    assert (await invoke(KickService(database, gateway), request_id=REQUEST + 1)).code == "not_sent"
    gateway.kick.assert_not_awaited()


@pytest.mark.parametrize("error", [
    discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Denied"),
    discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Member"),
    discord.HTTPException(SimpleNamespace(status=500, reason="Server error"), "Unconfirmed"),
    TimeoutError("remote timeout"), ConnectionResetError("connection reset"),
])
async def test_delete_errors_are_pending_and_never_retried(database, error):
    gateway = Gateway()
    gateway.kick.side_effect = error
    outcome = await invoke(KickService(database, gateway))
    assert outcome.code == "pending"
    assert (await database.case_history(GUILD))[0]["status"] == "pending"
    gateway.kick.assert_awaited_once()


async def test_delete_cancellation_keeps_pending_for_manual_diagnosis(database):
    gateway = Gateway()
    gateway.kick.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await invoke(KickService(database, gateway))
    assert (await database.case_history(GUILD))[0]["status"] == "pending"
    gateway.kick.assert_awaited_once()


async def test_claim_failure_prevents_delete_without_raw_storage_error(database, monkeypatch):
    gateway = Gateway()
    async def fail(*args, **kwargs):
        raise RuntimeError("private-storage-details")
    monkeypatch.setattr(database, "claim_case", fail)
    result = await invoke(KickService(database, gateway))
    assert result.code == "not_sent_unrecorded"
    assert "private-storage-details" not in result.message
    gateway.kick.assert_not_awaited()


@pytest.mark.parametrize("failure", ["exception", "cas_zero"])
async def test_delete_success_with_unconfirmed_finalization_is_not_reported_as_failed(database, monkeypatch, failure):
    gateway = Gateway()
    if failure == "exception":
        async def finalize(*args):
            raise RuntimeError("private-finalization-detail")
    else:
        async def finalize(*args):
            return 0
    monkeypatch.setattr(database, "finish_case", finalize)
    outcome = await invoke(KickService(database, gateway))
    assert outcome.code == "applied_unrecorded"
    assert "private-finalization-detail" not in outcome.message
    assert (await database.case_history(GUILD))[0]["status"] == "pending"
    gateway.kick.assert_awaited_once()


async def test_invalid_reason_and_scope_never_reach_gateway_or_database(database):
    gateway = Gateway()
    service = KickService(database, gateway)
    for change in ({"reason": " \t "}, {"reason": "x" * 501}, {"guild_id": 0},
                   {"actor_id": True}, {"target_id": 2**63}, {"request_id": -1}, {"reason": "nul\0reason"}):
        with pytest.raises(ModerationError):
            await invoke(service, **change)
    gateway.load.assert_not_awaited()
    gateway.kick.assert_not_awaited()
    assert await database.case_history(GUILD) == []


async def test_history_is_authorized_and_reads_former_member_without_target_fetch(database):
    case = await database.create_case(GUILD, TARGET, ACTOR, "warn", "Попередження", "applied")
    await database.create_case(GUILD + 1, TARGET, ACTOR, "warn", "Сусідній сервер", "applied")
    gateway = Gateway(initial=context(actor_permissions=MODERATE, bot_permissions=0, target=False))
    rows = await KickService(database, gateway).history(GUILD, ACTOR, user_id=TARGET)
    assert [row["id"] for row in rows] == [case]
    assert gateway.load.await_count == 1
    args, kwargs = gateway.load.await_args
    assert args[:2] == (GUILD, ACTOR)
    assert kwargs.get("target_id", args[2] if len(args) > 2 else None) is None
    gateway.kick.assert_not_awaited()


async def test_history_permission_loss_blocks_storage_read(database, monkeypatch):
    gateway = Gateway(initial=context(actor_permissions=0, target=False))
    read = AsyncMock()
    monkeypatch.setattr(database, "case_history", read)
    with pytest.raises(ModerationError):
        await KickService(database, gateway).history(GUILD, ACTOR)
    read.assert_not_awaited()


async def test_administrator_still_obeys_actor_role_hierarchy(database):
    gateway = Gateway(initial=context(actor_permissions=ADMIN, target=False),
                      final=context(actor_permissions=ADMIN))
    assert (await invoke(KickService(database, gateway))).code == "applied"
    lower = replace(context(actor_permissions=ADMIN), actor=member(ACTOR, 1, ADMIN))
    gateway = Gateway(initial=context(actor_permissions=ADMIN, target=False), final=lower)
    assert (await invoke(KickService(database, gateway), request_id=REQUEST + 1)).code == "not_sent"
    gateway.kick.assert_not_awaited()


@pytest.mark.parametrize("actor_role_id,target_role_id,expected", [(10, 20, "applied"), (20, 10, "not_sent")])
async def test_equal_role_positions_use_id_tie_breaker(database, actor_role_id, target_role_id, expected):
    final = replace(context(), actor=member(ACTOR, 10, KICK, role_id=actor_role_id),
                    target=member(TARGET, 10, role_id=target_role_id))
    gateway = Gateway(final=final)
    assert (await invoke(KickService(database, gateway))).code == expected
    assert gateway.kick.await_count == (expected == "applied")


async def test_owner_transfer_during_claim_does_not_preserve_old_owner_privileges(database):
    initial = replace(context(target=False), owner_id=ACTOR, actor=member(ACTOR, 1, 0))
    final = replace(context(), actor=member(ACTOR, 1, 0))
    gateway = Gateway(initial, final)
    assert (await invoke(KickService(database, gateway))).code == "not_sent"
    gateway.kick.assert_not_awaited()


async def test_target_read_failure_never_reaches_delete(database):
    gateway = Gateway()
    async def fetch(guild_id, actor_id, target_id=None):
        if target_id is not None:
            raise ModerationError("Учасника більше немає на сервері.")
        return gateway.initial
    gateway.load.side_effect = fetch
    result = await invoke(KickService(database, gateway))
    assert result.code == "not_sent"
    assert (await database.case_history(GUILD))[0]["status"] == "failed"
    gateway.kick.assert_not_awaited()


async def test_preflight_failure_and_storage_failure_do_not_send_delete(database, monkeypatch):
    gateway = Gateway(final=context(actor_permissions=0))
    async def fail_finalize(*args):
        raise RuntimeError("private failed case write")
    monkeypatch.setattr(database, "finish_case", fail_finalize)
    result = await invoke(KickService(database, gateway))
    assert result.code == "not_sent_unrecorded"
    assert "private failed case write" not in result.message
    assert (await database.case_history(GUILD))[0]["status"] == "pending"
    gateway.kick.assert_not_awaited()


@pytest.mark.parametrize("permission", [KICK, BAN, MODERATE])
async def test_each_supported_moderation_permission_can_read_history(database, permission):
    case = await database.create_case(GUILD, TARGET, ACTOR, "kick", "Історія", "applied")
    gateway = Gateway(initial=context(actor_permissions=permission, bot_permissions=0, target=False))
    rows = await KickService(database, gateway).history(GUILD, ACTOR)
    assert [row["id"] for row in rows] == [case]
    gateway.kick.assert_not_awaited()


async def test_unicode_audit_header_is_encoded_bounded_while_database_keeps_full_reason(database):
    from urllib.parse import quote
    gateway = Gateway()
    reason = "Ї🚀" * 250
    result = await invoke(KickService(database, gateway), reason=reason)
    assert result.code == "applied"
    audit_reason = gateway.kick.await_args.args[2]
    assert result.case_id in audit_reason and str(ACTOR) in audit_reason
    # discord.py percent-encodes this header, so a raw 512-character slice is unsafe.
    assert len(quote(audit_reason, safe="/ ")) <= 512
    assert (await database.case_history(GUILD))[0]["reason"] == reason


async def test_repeated_request_with_changed_payload_never_sends_a_second_delete(database):
    gateway = Gateway()
    service = KickService(database, gateway)
    first = await invoke(service)
    second = await invoke(service, reason="Змінена причина")
    assert second.code == "not_sent_unrecorded"
    assert second.case_id == first.case_id
    assert (await database.case_history(GUILD))[0]["reason"] == "Порушення правил"
    gateway.kick.assert_awaited_once()


async def test_invalid_history_filters_never_reach_gateway_or_database(database, monkeypatch):
    gateway = Gateway()
    read = AsyncMock()
    monkeypatch.setattr(database, "case_history", read)
    service = KickService(database, gateway)
    for options in ({"page": 0}, {"page": True}, {"status": "invented"},
                    {"status": []}, {"status": {}}, {"user_id": 2**63}):
        with pytest.raises(ModerationError):
            await service.history(GUILD, ACTOR, **options)
    gateway.load.assert_not_awaited()
    read.assert_not_awaited()


async def test_history_storage_failure_returns_safe_message(database, monkeypatch):
    gateway = Gateway()
    read = AsyncMock(side_effect=RuntimeError("private-history-storage-error"))
    monkeypatch.setattr(database, "case_history", read)
    with pytest.raises(ModerationError) as error:
        await KickService(database, gateway).history(GUILD, ACTOR)
    assert "private-history-storage-error" not in str(error.value)
    gateway.kick.assert_not_awaited()
