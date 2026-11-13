"""Ban/unban invariants with committed SQLite cases and no Discord connection."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import discord
import pytest

from core_v2.db import Database
from core_v2.moderation import ADMIN, BAN, KICK, MODERATE, Context, MemberState, ModerationError, ModerationService

GUILD, ACTOR, BOT, TARGET, OWNER, REQUEST = 110, 220, 330, 440, 990, 880


def member(identifier, position, permissions=0, guild_id=GUILD):
    return MemberState(identifier, guild_id, (1, position, -identifier), permissions)


def context(*, actor_permissions=BAN, bot_permissions=BAN, target=True, owner_id=OWNER):
    return Context(GUILD, owner_id, member(ACTOR, 20, actor_permissions),
                   member(BOT, 30, bot_permissions), member(TARGET, 10) if target else None)


class Gateway:
    def __init__(self, *, initial=None, final=None, banned=True):
        self.initial = initial if initial is not None else context(target=False)
        self.final = final if final is not None else context()
        self.authority_reads = 0
        self.load = AsyncMock(side_effect=self._authority)
        self.load_ban = AsyncMock(return_value=self.final)
        self.ban_exists = AsyncMock(return_value=banned)
        self.ban = AsyncMock()
        self.unban = AsyncMock()

    async def _authority(self, guild_id, actor_id):
        self.authority_reads += 1
        return self.initial if self.authority_reads == 1 else self.final


@pytest.fixture
async def database(tmp_path):
    database = Database("sqlite+aiosqlite:///" + str(tmp_path / "ban audit з пробілами.db"))
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()


async def invoke(service, action, **changes):
    arguments = dict(guild_id=GUILD, actor_id=ACTOR, target_id=TARGET,
                     request_id=REQUEST, reason="  Порушення правил сервера  ")
    arguments.update(changes)
    return await getattr(service, action)(**arguments)


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_committed_audit_precedes_remote_action_and_success_is_recorded(database, action):
    gateway = Gateway()

    async def mutate(snapshot, target_id, audit_reason):
        rows = await database.case_history(GUILD)
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "pending" and row["action"] == action
        assert row["reason"] == "Порушення правил сервера"
        assert row["actor_id"] == ACTOR and row["user_id"] == target_id == TARGET
        assert row["id"] in audit_reason and str(ACTOR) in audit_reason
        assert snapshot.guild_id == GUILD

    getattr(gateway, action).side_effect = mutate
    result = await invoke(ModerationService(database, gateway), action)
    assert result.code == "applied" and result.status == "applied"
    row = (await database.case_history(GUILD))[0]
    assert row["id"] == result.case_id and row["status"] == "applied"
    getattr(gateway, action).assert_awaited_once()
    if action == "ban":
        gateway.load_ban.assert_awaited_once_with(GUILD, ACTOR, TARGET)
    else:
        assert gateway.load.await_count == 2
        gateway.ban_exists.assert_awaited_once()


async def test_ban_of_proven_absent_member_needs_no_invented_hierarchy(database):
    gateway = Gateway(final=context(target=False))
    result = await invoke(ModerationService(database, gateway), "ban")
    assert result.code == "applied"
    gateway.ban.assert_awaited_once()
    assert (await database.case_history(GUILD))[0]["user_id"] == TARGET


async def test_unban_uses_ban_record_without_fetching_current_member(database):
    gateway = Gateway(final=context(target=False))
    result = await invoke(ModerationService(database, gateway), "unban")
    assert result.code == "applied"
    gateway.load_ban.assert_not_awaited()
    assert all(call.args == (GUILD, ACTOR) for call in gateway.load.await_args_list)
    gateway.ban_exists.assert_awaited_once()
    gateway.unban.assert_awaited_once()


async def test_missing_ban_is_explicitly_not_sent_and_never_calls_mutation(database):
    gateway = Gateway(banned=False)
    result = await invoke(ModerationService(database, gateway), "unban")
    assert result.code == "not_sent" and result.status == "failed"
    assert (await database.case_history(GUILD))[0]["status"] == "failed"
    gateway.unban.assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_concurrent_delivery_claims_one_owner_and_one_remote_action(database, action):
    gateway = Gateway()
    service = ModerationService(database, gateway)
    results = await asyncio.gather(*(invoke(service, action) for _ in range(8)))
    assert [result.code for result in results].count("applied") == 1
    assert len({result.case_id for result in results}) == 1
    assert len(await database.case_history(GUILD)) == 1
    getattr(gateway, action).assert_awaited_once()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_restart_replay_checks_authority_but_never_repeats_remote_action(database, action):
    gateway = Gateway()
    first = await invoke(ModerationService(database, gateway), action)
    fresh = Gateway()
    fresh.load_ban.side_effect = AssertionError("A completed claim must not require target membership")
    fresh.ban_exists.side_effect = AssertionError("A completed claim must not query the new ban state")
    repeated = await invoke(ModerationService(database, fresh), action)
    assert repeated.code == "duplicate" and repeated.status == "applied"
    assert repeated.case_id == first.case_id
    fresh.load.assert_awaited_once_with(GUILD, ACTOR)
    getattr(fresh, action).assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_pending_replay_does_not_guess_from_current_remote_state(database, action):
    gateway = Gateway()
    getattr(gateway, action).side_effect = TimeoutError("private timeout details")
    first = await invoke(ModerationService(database, gateway), action)
    assert first.code == "pending"
    restarted = Gateway(banned=False)
    repeated = await invoke(ModerationService(database, restarted), action)
    assert repeated.code == "duplicate" and repeated.status == "pending"
    restarted.ban_exists.assert_not_awaited()
    getattr(restarted, action).assert_not_awaited()
    assert (await database.case_history(GUILD))[0]["status"] == "pending"


async def test_same_interaction_cannot_be_rebound_to_another_action(database):
    gateway = Gateway()
    service = ModerationService(database, gateway)
    first = await invoke(service, "ban")
    second = await invoke(service, "unban")
    assert second.case_id == first.case_id and second.code == "not_sent_unrecorded"
    assert (await database.case_history(GUILD))[0]["action"] == "ban"
    gateway.ban.assert_awaited_once()
    gateway.unban.assert_not_awaited()


async def test_competing_cross_action_claims_cannot_send_both_operations(database):
    gateway = Gateway()
    service = ModerationService(database, gateway)
    results = await asyncio.gather(invoke(service, "ban"), invoke(service, "unban"))
    assert sum(result.code == "applied" for result in results) == 1
    assert gateway.ban.await_count + gateway.unban.await_count == 1
    rows = await database.case_history(GUILD)
    assert len(rows) == 1 and rows[0]["status"] == "applied"


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_changed_reason_cannot_overwrite_a_previous_receipt(database, action):
    gateway = Gateway()
    service = ModerationService(database, gateway)
    await invoke(service, action)
    result = await invoke(service, action, reason="Інша причина")
    assert result.code == "not_sent_unrecorded"
    assert (await database.case_history(GUILD))[0]["reason"] == "Порушення правил сервера"
    getattr(gateway, action).assert_awaited_once()


@pytest.mark.parametrize("action", ["ban", "unban"])
@pytest.mark.parametrize("permissions", [(0, BAN), (BAN, 0), (KICK, BAN), (MODERATE, BAN)],
                         ids=["actor-missing", "bot-missing", "kick-only", "timeout-only"])
async def test_initial_ban_permission_is_required_before_any_case_write(database, action, permissions):
    gateway = Gateway(initial=context(actor_permissions=permissions[0], bot_permissions=permissions[1], target=False))
    with pytest.raises(ModerationError):
        await invoke(ModerationService(database, gateway), action)
    assert await database.case_history(GUILD) == []
    getattr(gateway, action).assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
@pytest.mark.parametrize("lost", ["actor", "bot"])
async def test_permission_revocation_after_claim_blocks_the_remote_write(database, action, lost):
    final = context(actor_permissions=0) if lost == "actor" else context(bot_permissions=0)
    gateway = Gateway(final=final)
    result = await invoke(ModerationService(database, gateway), action)
    assert result.code == "not_sent"
    assert (await database.case_history(GUILD))[0]["status"] == "failed"
    getattr(gateway, action).assert_not_awaited()


@pytest.mark.parametrize("part", ["actor", "bot", "target", "guild"])
async def test_ban_context_from_another_guild_cannot_mutate_or_reveal_cases(database, part):
    final = context()
    if part == "guild":
        final = replace(final, guild_id=GUILD + 1)
    else:
        final = replace(final, **{part: replace(getattr(final, part), guild_id=GUILD + 1)})
    gateway = Gateway(final=final)
    result = await invoke(ModerationService(database, gateway), "ban")
    assert result.code == "not_sent"
    gateway.ban.assert_not_awaited()
    assert await database.case_history(GUILD + 1) == []


@pytest.mark.parametrize("part", ["actor", "bot"])
@pytest.mark.parametrize("rank_change", ["below", "equal"])
async def test_member_ban_still_respects_actor_and_bot_hierarchy(database, part, rank_change):
    final = context()
    current = getattr(final, part)
    rank = member(current.id, 9, BAN).rank if rank_change == "below" else final.target.rank
    final = replace(final, **{part: replace(current, rank=rank)})
    gateway = Gateway(final=final)
    assert (await invoke(ModerationService(database, gateway), "ban")).code == "not_sent"
    gateway.ban.assert_not_awaited()


async def test_administrator_permission_does_not_bypass_actor_hierarchy(database):
    final = replace(context(actor_permissions=ADMIN), actor=member(ACTOR, 1, ADMIN))
    gateway = Gateway(initial=context(actor_permissions=ADMIN, target=False), final=final)
    assert (await invoke(ModerationService(database, gateway), "ban")).code == "not_sent"
    gateway.ban.assert_not_awaited()


async def test_owner_bypasses_own_rank_but_never_bot_rank_for_member_ban(database):
    initial = replace(context(target=False, owner_id=ACTOR), actor=member(ACTOR, 1, 0))
    final = replace(context(owner_id=ACTOR), actor=member(ACTOR, 1, 0))
    gateway = Gateway(initial=initial, final=final)
    service = ModerationService(database, gateway)
    assert (await invoke(service, "ban")).code == "applied"
    low_bot = Gateway(initial=initial, final=replace(final, bot=member(BOT, 1, BAN)))
    result = await invoke(ModerationService(database, low_bot), "ban", request_id=REQUEST + 1)
    assert result.code == "not_sent"
    low_bot.ban.assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_owner_transfer_during_claim_revokes_old_owner_authority(database, action):
    initial = replace(context(target=False, owner_id=ACTOR), actor=member(ACTOR, 1, 0))
    gateway = Gateway(initial=initial, final=context(actor_permissions=0))
    assert (await invoke(ModerationService(database, gateway), action)).code == "not_sent"
    getattr(gateway, action).assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
@pytest.mark.parametrize("target_id", [ACTOR, BOT, OWNER], ids=["self", "bot", "owner"])
async def test_protected_identity_never_reaches_mutation_even_if_target_has_no_roles(database, action, target_id):
    gateway = Gateway(final=context(target=False))
    try:
        result = await invoke(ModerationService(database, gateway), action, target_id=target_id)
        assert result.code == "not_sent"
    except ModerationError:
        pass
    getattr(gateway, action).assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_owner_transfer_to_target_after_claim_protects_new_owner(database, action):
    gateway = Gateway(final=context(owner_id=TARGET, target=False))
    result = await invoke(ModerationService(database, gateway), action)
    assert result.code == "not_sent"
    getattr(gateway, action).assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
@pytest.mark.parametrize("error", [
    discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "private remote error"),
    discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), {"code": 10026, "message": "private remote error"}),
    discord.HTTPException(SimpleNamespace(status=500, reason="Server Error"), "private remote error"),
    TimeoutError("private remote error"), ConnectionResetError("private remote error"),
], ids=["forbidden", "not-found", "server-error", "timeout", "connection-reset"])
async def test_mutation_errors_keep_pending_without_application_retry_or_raw_payload(database, action, error, caplog):
    gateway = Gateway()
    getattr(gateway, action).side_effect = error
    result = await invoke(ModerationService(database, gateway), action)
    assert result.code == "pending" and result.status == "pending"
    assert "private remote error" not in result.message
    assert "private remote error" not in caplog.text
    assert (await database.case_history(GUILD))[0]["status"] == "pending"
    getattr(gateway, action).assert_awaited_once()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_cancellation_after_mutation_started_propagates_and_preserves_pending(database, action):
    gateway = Gateway()
    getattr(gateway, action).side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await invoke(ModerationService(database, gateway), action)
    assert (await database.case_history(GUILD))[0]["status"] == "pending"
    getattr(gateway, action).assert_awaited_once()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_cancelled_preflight_after_claim_never_sends_mutation(database, action):
    gateway = Gateway()
    reader = gateway.load_ban if action == "ban" else gateway.ban_exists
    reader.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await invoke(ModerationService(database, gateway), action)
    assert (await database.case_history(GUILD))[0]["status"] == "pending"
    getattr(gateway, action).assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_cancelled_initial_authorization_does_not_claim_a_case(database, action):
    gateway = Gateway()
    gateway.load.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await invoke(ModerationService(database, gateway), action)
    assert await database.case_history(GUILD) == []
    getattr(gateway, action).assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_failed_claim_never_sends_mutation_or_exposes_storage_details(database, monkeypatch, action):
    gateway = Gateway()
    async def fail(*args):
        raise RuntimeError("private claim credentials")
    monkeypatch.setattr(database, "claim_case", fail)
    result = await invoke(ModerationService(database, gateway), action)
    assert result.code == "not_sent_unrecorded"
    assert "private claim credentials" not in result.message
    getattr(gateway, action).assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
@pytest.mark.parametrize("failure", ["exception", "cas-zero"])
async def test_successful_remote_action_cannot_be_reported_failed_when_finalization_is_unknown(database, monkeypatch, action, failure):
    gateway = Gateway()
    async def finish(*args):
        if failure == "exception":
            raise RuntimeError("private finalization credentials")
        return 0
    monkeypatch.setattr(database, "finish_case", finish)
    result = await invoke(ModerationService(database, gateway), action)
    assert result.code == "applied_unrecorded"
    assert "private finalization credentials" not in result.message
    assert (await database.case_history(GUILD))[0]["status"] == "pending"
    getattr(gateway, action).assert_awaited_once()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_preflight_read_failure_records_not_sent_and_hides_raw_exception(database, action):
    gateway = Gateway()
    reader = gateway.load_ban if action == "ban" else gateway.ban_exists
    reader.side_effect = OSError("private endpoint details")
    result = await invoke(ModerationService(database, gateway), action)
    assert result.code == "not_sent" and "private endpoint details" not in result.message
    assert (await database.case_history(GUILD))[0]["status"] == "failed"
    getattr(gateway, action).assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_invalid_ids_and_reasons_are_refused_before_remote_reads_or_case_write(database, action):
    gateway = Gateway()
    service = ModerationService(database, gateway)
    for change in ({"reason": " \t "}, {"reason": "x" * 501}, {"reason": "nul\0reason"},
                   {"reason": None}, {"guild_id": False}, {"actor_id": 0},
                   {"target_id": 2**63}, {"request_id": -1}):
        with pytest.raises(ModerationError):
            await invoke(service, action, **change)
    gateway.load.assert_not_awaited()
    assert await database.case_history(GUILD) == []
    getattr(gateway, action).assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_maximum_unicode_reason_is_preserved_with_bounded_encoded_audit_header(database, action):
    gateway = Gateway()
    reason = "Ї🚀" * 250
    result = await invoke(ModerationService(database, gateway), action, reason=reason)
    assert result.code == "applied"
    text = getattr(gateway, action).await_args.args[2]
    assert result.case_id in text and str(ACTOR) in text
    assert len(quote(text, safe="/ ")) <= 512
    assert (await database.case_history(GUILD))[0]["reason"] == reason


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_a_new_interaction_is_a_distinct_request_and_cannot_change_old_case(database, action):
    gateway = Gateway()
    service = ModerationService(database, gateway)
    first = await invoke(service, action)
    second = await invoke(service, action, request_id=REQUEST + 1, reason="Нова явна дія")
    assert first.case_id != second.case_id
    assert first.code == second.code == "applied"
    assert getattr(gateway, action).await_count == 2
    rows = await database.case_history(GUILD)
    assert {row["reason"] for row in rows} == {"Порушення правил сервера", "Нова явна дія"}


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_same_interaction_in_another_guild_has_a_separate_receipt(database, action):
    first = await invoke(ModerationService(database, Gateway()), action)
    other = context()
    other = replace(other, guild_id=GUILD + 1,
                    actor=replace(other.actor, guild_id=GUILD + 1),
                    bot=replace(other.bot, guild_id=GUILD + 1),
                    target=replace(other.target, guild_id=GUILD + 1))
    gateway = Gateway(initial=replace(other, target=None), final=other)
    second = await invoke(ModerationService(database, gateway), action,
                          guild_id=GUILD + 1, reason="Інший сервер")
    assert first.code == second.code == "applied" and first.case_id != second.case_id
    assert [row["id"] for row in await database.case_history(GUILD)] == [first.case_id]
    assert [row["id"] for row in await database.case_history(GUILD + 1)] == [second.case_id]


@pytest.mark.parametrize("value", [None, 1, "yes", {}], ids=["unknown", "numeric", "text", "mapping"])
async def test_unconfirmed_ban_record_is_not_a_license_to_unban(database, value):
    gateway = Gateway(banned=value)
    result = await invoke(ModerationService(database, gateway), "unban")
    assert result.code == "not_sent"
    assert (await database.case_history(GUILD))[0]["status"] == "failed"
    gateway.unban.assert_not_awaited()
