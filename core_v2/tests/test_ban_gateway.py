"""Ban adapter contract through actual discord.py models and offline HTTP doubles."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core_v2.discord_moderation import history_embed
from core_v2.moderation import ModerationError
from test_discord_moderation import (
    ACTOR, ACTOR_ROLE, BOT, GUILD, TARGET,
    history_row, rest_snapshot as rest_snapshot, user_payload,
)


def api_error(status, code, kind=discord.NotFound):
    return kind(SimpleNamespace(status=status, reason="offline API failure"),
                {"code": code, "message": "private-api-payload"})


def fail_member(state, identifier, error):
    original = state.get_member.side_effect

    async def fetch(guild_id, user_id):
        if user_id == identifier:
            raise error
        return original(guild_id, user_id)

    state.get_member.side_effect = fetch


async def test_ban_load_uses_fresh_target_roles_without_mutating_gateway_cache(rest_snapshot):
    state = rest_snapshot
    old_roles = state.cached._roles.copy()
    state.fresh["roles"][1]["permissions"] = str(discord.Permissions(ban_members=True).value)
    context = await state.gateway.load_ban(GUILD, ACTOR, TARGET)
    assert context.target.id == TARGET
    assert context.actor.permissions & discord.Permissions(ban_members=True).value
    assert context.actor.rank > context.target.rank
    assert context.handle is not state.cached
    assert state.cached._roles == old_roles
    assert not state.cached.get_role(ACTOR_ROLE).permissions.ban_members
    assert {(call.args[0], call.args[1]) for call in state.get_member.await_args_list} == {
        (GUILD, ACTOR), (GUILD, BOT), (GUILD, TARGET)}
    state.get_guild.assert_awaited_once_with(GUILD, with_counts=False)


async def test_ban_load_allows_only_known_unknown_member_target(rest_snapshot):
    state = rest_snapshot
    fail_member(state, TARGET, api_error(404, 10007))
    context = await state.gateway.load_ban(GUILD, ACTOR, TARGET)
    assert context.target is None
    assert context.actor.id == ACTOR and context.bot.id == BOT
    assert context.guild_id == GUILD
    # Kick remains strict: an absent target is permitted only for the ban flow.
    with pytest.raises(ModerationError):
        await state.gateway.load(GUILD, ACTOR, TARGET)


@pytest.mark.parametrize("error", [
    api_error(404, 10004), api_error(404, 10013), api_error(404, 0),
    api_error(403, 10007), api_error(403, 50013, discord.Forbidden),
    api_error(500, 0, discord.HTTPException), RuntimeError("private-api-payload"),
], ids=["unknown-guild", "unknown-user", "unspecified-404", "wrong-status",
        "forbidden", "server-error", "transport-error"])
async def test_ban_target_read_failures_are_not_treated_as_absent(rest_snapshot, error):
    state = rest_snapshot
    fail_member(state, TARGET, error)
    with pytest.raises(ModerationError) as failure:
        await state.gateway.load_ban(GUILD, ACTOR, TARGET)
    assert "private-api-payload" not in str(failure.value)


@pytest.mark.parametrize("identifier", [ACTOR, BOT])
@pytest.mark.parametrize("same_target", [False, True])
async def test_absent_actor_or_bot_is_never_masked_even_when_target_matches(rest_snapshot, identifier, same_target):
    state = rest_snapshot
    fail_member(state, identifier, api_error(404, 10007))
    with pytest.raises(ModerationError):
        await state.gateway.load_ban(GUILD, ACTOR, identifier if same_target else TARGET)


@pytest.mark.parametrize("identifier", [ACTOR, BOT, TARGET])
async def test_ban_load_refuses_unknown_roles_in_any_member(rest_snapshot, identifier):
    state = rest_snapshot
    state.members[identifier]["roles"].append("999999")
    with pytest.raises(ModerationError):
        await state.gateway.load_ban(GUILD, ACTOR, TARGET)


@pytest.mark.parametrize("identifier", [ACTOR, BOT, TARGET])
async def test_ban_load_refuses_incorrect_member_identity(rest_snapshot, identifier):
    state = rest_snapshot
    state.members[identifier]["user"]["id"] = str(identifier + 1)
    with pytest.raises(ModerationError):
        await state.gateway.load_ban(GUILD, ACTOR, TARGET)


async def test_ban_load_refuses_foreign_guild_before_member_requests(rest_snapshot):
    state = rest_snapshot
    state.fresh["id"] = str(GUILD + 1)
    with pytest.raises(ModerationError):
        await state.gateway.load_ban(GUILD, ACTOR, TARGET)
    state.get_member.assert_not_awaited()


async def test_ban_load_requires_ready_client_before_http(rest_snapshot):
    state = rest_snapshot
    state.client._connection.user = None
    with pytest.raises(ModerationError):
        await state.gateway.load_ban(GUILD, ACTOR, TARGET)
    state.get_guild.assert_not_awaited()


async def test_ban_load_cancels_and_joins_sibling_reads_after_authority_failure(rest_snapshot):
    state = rest_snapshot
    started = {identifier: asyncio.Event() for identifier in (BOT, TARGET)}
    cancelled = set()

    async def fetch(guild_id, identifier):
        if identifier == ACTOR:
            await asyncio.gather(*(event.wait() for event in started.values()))
            raise api_error(404, 10007)
        started[identifier].set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.add(identifier)
            raise

    state.get_member.side_effect = fetch
    with pytest.raises(ModerationError):
        await asyncio.wait_for(state.gateway.load_ban(GUILD, ACTOR, TARGET), 2)
    assert cancelled == {BOT, TARGET}


async def test_ban_load_cancellation_joins_all_member_reads(rest_snapshot):
    state = rest_snapshot
    started, cancelled = set(), set()

    async def fetch(guild_id, identifier):
        started.add(identifier)
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.add(identifier)
            raise

    state.get_member.side_effect = fetch
    operation = asyncio.create_task(state.gateway.load_ban(GUILD, ACTOR, TARGET))
    try:
        async with asyncio.timeout(2):
            while len(started) < 3:
                await asyncio.sleep(0)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert cancelled == {ACTOR, BOT, TARGET}
    finally:
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


async def test_ban_explicitly_preserves_messages_and_scopes_endpoint_and_reason(rest_snapshot, monkeypatch):
    state = rest_snapshot
    ban = AsyncMock()
    monkeypatch.setattr(state.client.http, "ban", ban)
    context = await state.gateway.load_ban(GUILD, ACTOR, TARGET)
    reason = "case=offline-case; actor=100; Українська причина"
    await state.gateway.ban(context, TARGET, reason)
    # The actual Guild.ban default is 86400 seconds: this must be an explicit zero.
    ban.assert_awaited_once_with(TARGET, GUILD, 0, reason=reason)


async def test_unban_scopes_endpoint_and_preserves_reason_without_target_member_fetch(rest_snapshot, monkeypatch):
    state = rest_snapshot
    unban = AsyncMock()
    monkeypatch.setattr(state.client.http, "unban", unban)
    del state.members[TARGET]
    context = await state.gateway.load(GUILD, ACTOR)
    reason = "case=offline-case; actor=100; Скасування блокування"
    await state.gateway.unban(context, TARGET, reason)
    unban.assert_awaited_once_with(TARGET, GUILD, reason=reason)
    assert {call.args[1] for call in state.get_member.await_args_list} == {ACTOR, BOT}


async def test_existing_ban_read_uses_real_ban_entry_and_current_guild(rest_snapshot, monkeypatch):
    state = rest_snapshot
    get_ban = AsyncMock(return_value={"user": user_payload(TARGET), "reason": "Стара причина"})
    monkeypatch.setattr(state.client.http, "get_ban", get_ban)
    context = await state.gateway.load(GUILD, ACTOR)
    assert await state.gateway.ban_exists(context, TARGET) is True
    get_ban.assert_awaited_once_with(TARGET, GUILD)


async def test_only_precise_unknown_ban_is_reported_absent(rest_snapshot, monkeypatch):
    state = rest_snapshot
    get_ban = AsyncMock(side_effect=api_error(404, 10026))
    monkeypatch.setattr(state.client.http, "get_ban", get_ban)
    context = await state.gateway.load(GUILD, ACTOR)
    assert await state.gateway.ban_exists(context, TARGET) is False


@pytest.mark.parametrize("error", [
    api_error(404, 10004), api_error(404, 10013), api_error(404, 0),
    api_error(403, 10026), api_error(403, 50013, discord.Forbidden),
    api_error(500, 0, discord.HTTPException), RuntimeError("private-api-payload"),
], ids=["unknown-guild", "unknown-user", "unspecified-404", "wrong-status",
        "forbidden", "server-error", "transport-error"])
async def test_ban_read_error_does_not_claim_absence_or_expose_payload(rest_snapshot, monkeypatch, error):
    state = rest_snapshot
    monkeypatch.setattr(state.client.http, "get_ban", AsyncMock(side_effect=error))
    context = await state.gateway.load(GUILD, ACTOR)
    with pytest.raises(ModerationError) as failure:
        await state.gateway.ban_exists(context, TARGET)
    assert "private-api-payload" not in str(failure.value)


async def test_ban_read_refuses_wrong_user_identity(rest_snapshot, monkeypatch):
    state = rest_snapshot
    monkeypatch.setattr(state.client.http, "get_ban", AsyncMock(return_value={
        "user": user_payload(TARGET + 1), "reason": "private-api-payload"}))
    context = await state.gateway.load(GUILD, ACTOR)
    with pytest.raises(ModerationError) as failure:
        await state.gateway.ban_exists(context, TARGET)
    assert "private-api-payload" not in str(failure.value)


async def test_ban_read_cancellation_propagates(rest_snapshot, monkeypatch):
    state = rest_snapshot
    monkeypatch.setattr(state.client.http, "get_ban", AsyncMock(side_effect=asyncio.CancelledError))
    context = await state.gateway.load(GUILD, ACTOR)
    with pytest.raises(asyncio.CancelledError):
        await state.gateway.ban_exists(context, TARGET)


@pytest.mark.parametrize("action,label", [("ban", "Блокування"), ("unban", "Зняття блокування")])
def test_history_labels_ban_and_unban_without_claiming_pending_success(action, label):
    embed = history_embed([history_row(action=action)], 1)
    assert embed.fields[0].name.startswith(label + " ·")
    assert "Непідтверджено" in embed.fields[0].value
