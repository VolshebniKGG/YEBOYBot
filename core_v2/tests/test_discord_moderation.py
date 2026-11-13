"""Fresh REST snapshots through real discord.py models, without Discord network calls."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core_v2.discord_moderation import DiscordGateway
from core_v2.moderation import ModerationError

GUILD, ACTOR, BOT, TARGET, OWNER = 10, 100, 200, 300, 900
ACTOR_ROLE, BOT_ROLE, TARGET_ROLE = 101, 201, 301
KICK = discord.Permissions(kick_members=True).value


def user_payload(user_id, *, bot=False):
    return {"id": str(user_id), "username": f"fixture-{user_id}",
            "discriminator": "0", "avatar": None, "bot": bot}


def role_payload(role_id, position, permissions=0):
    return {"id": str(role_id), "name": f"role-{role_id}", "position": position,
            "permissions": str(permissions)}


def guild_payload(*, owner_id=OWNER, actor_permissions=KICK, actor_position=10,
                  bot_position=20, target_position=5, guild_id=GUILD):
    return {"id": str(guild_id), "name": "Offline guild", "owner_id": str(owner_id),
            "roles": [role_payload(guild_id, 0, discord.Permissions(view_channel=True).value),
                      role_payload(ACTOR_ROLE, actor_position, actor_permissions),
                      role_payload(BOT_ROLE, bot_position, KICK),
                      role_payload(TARGET_ROLE, target_position)]}


def member_payload(user_id, role_ids=(), *, timed_out=False):
    data = {"user": user_payload(user_id, bot=user_id == BOT),
            "roles": [str(role_id) for role_id in role_ids], "flags": 0,
            "joined_at": "2025-01-01T00:00:00+00:00"}
    if timed_out:
        data["communication_disabled_until"] = (discord.utils.utcnow() + timedelta(hours=1)).isoformat()
    return data


@pytest.fixture
async def rest_snapshot(monkeypatch):
    client = discord.Client(intents=discord.Intents.none())
    client._connection.user = discord.ClientUser(state=client._connection, data=user_payload(BOT, bot=True))
    stale = guild_payload(actor_permissions=0, actor_position=1, bot_position=2, target_position=99)
    cached = discord.Guild(state=client._connection, data=stale)
    client._connection._guilds[GUILD] = cached
    fresh = guild_payload()
    members = {ACTOR: member_payload(ACTOR, [ACTOR_ROLE]),
               BOT: member_payload(BOT, [BOT_ROLE]),
               TARGET: member_payload(TARGET, [TARGET_ROLE])}
    get_guild = AsyncMock(side_effect=lambda *a, **k: fresh)
    get_member = AsyncMock(side_effect=lambda guild_id, user_id: members[user_id])
    kick = AsyncMock()
    monkeypatch.setattr(client.http, "get_guild", get_guild)
    monkeypatch.setattr(client.http, "get_member", get_member)
    monkeypatch.setattr(client.http, "kick", kick)
    fixture = SimpleNamespace(client=client, gateway=DiscordGateway(client), cached=cached,
                              fresh=fresh, members=members, get_guild=get_guild,
                              get_member=get_member, kick=kick)
    try:
        yield fixture
    finally:
        await client.close()


async def test_load_uses_detached_rest_guild_and_does_not_mutate_cached_roles(rest_snapshot):
    state = rest_snapshot
    cached_roles = state.cached._roles.copy()
    context = await state.gateway.load(GUILD, ACTOR, TARGET)
    state.get_guild.assert_awaited_once_with(GUILD, with_counts=False)
    assert {(call.args[0], call.args[1]) for call in state.get_member.await_args_list} == {
        (GUILD, ACTOR), (GUILD, BOT), (GUILD, TARGET)}
    assert context.guild_id == GUILD and context.owner_id == OWNER
    assert context.actor.id == ACTOR and context.bot.id == BOT and context.target.id == TARGET
    assert context.actor.permissions & KICK
    assert context.actor.rank > context.target.rank
    assert context.bot.rank > context.actor.rank
    assert state.client.get_guild(GUILD) is state.cached
    assert state.cached._roles == cached_roles
    assert state.cached.get_role(ACTOR_ROLE).permissions.kick_members is False
    assert state.cached.get_role(TARGET_ROLE).position == 99
    assert context.handle is not state.cached
    state.kick.assert_not_awaited()


async def test_authority_only_load_does_not_require_current_target_membership(rest_snapshot):
    state = rest_snapshot
    del state.members[TARGET]
    context = await state.gateway.load(GUILD, ACTOR)
    assert context.target is None
    assert {call.args[1] for call in state.get_member.await_args_list} == {ACTOR, BOT}


async def test_fresh_owner_metadata_controls_owner_permissions(rest_snapshot):
    state = rest_snapshot
    state.fresh.update(guild_payload(owner_id=ACTOR, actor_permissions=0, actor_position=0))
    context = await state.gateway.load(GUILD, ACTOR, TARGET)
    assert state.cached.owner_id == OWNER
    assert context.owner_id == ACTOR
    assert context.actor.permissions & KICK


async def test_current_timeout_masks_nonadministrator_kick_permission(rest_snapshot):
    state = rest_snapshot
    state.members[ACTOR] = member_payload(ACTOR, [ACTOR_ROLE], timed_out=True)
    context = await state.gateway.load(GUILD, ACTOR, TARGET)
    assert not context.actor.permissions & KICK


async def test_administrator_semantics_follow_discord_py_even_with_timeout(rest_snapshot):
    state = rest_snapshot
    admin = discord.Permissions(administrator=True).value
    state.fresh.update(guild_payload(actor_permissions=admin))
    state.members[ACTOR] = member_payload(ACTOR, [ACTOR_ROLE], timed_out=True)
    context = await state.gateway.load(GUILD, ACTOR, TARGET)
    assert context.actor.permissions & KICK


async def test_equal_role_positions_use_inverse_id_tie_breaker(rest_snapshot):
    state = rest_snapshot
    state.fresh.update(guild_payload(actor_position=10, target_position=10))
    context = await state.gateway.load(GUILD, ACTOR, TARGET)
    assert ACTOR_ROLE < TARGET_ROLE
    assert context.actor.rank > context.target.rank


async def test_everyone_is_lower_than_any_other_role_at_same_position(rest_snapshot):
    state = rest_snapshot
    state.fresh.update(guild_payload(actor_position=0, target_position=0))
    state.members[TARGET] = member_payload(TARGET)
    context = await state.gateway.load(GUILD, ACTOR, TARGET)
    assert context.actor.rank > context.target.rank


@pytest.mark.parametrize("user_id", [ACTOR, BOT, TARGET])
async def test_unknown_raw_role_ids_fail_closed_before_any_kick(rest_snapshot, user_id):
    state = rest_snapshot
    state.members[user_id]["roles"].append("999999")
    with pytest.raises(ModerationError):
        await state.gateway.load(GUILD, ACTOR, TARGET)
    state.kick.assert_not_awaited()


async def test_wrong_guild_response_is_not_accepted(rest_snapshot):
    state = rest_snapshot
    state.fresh.update(guild_payload(guild_id=GUILD + 1))
    with pytest.raises(ModerationError):
        await state.gateway.load(GUILD, ACTOR, TARGET)
    state.kick.assert_not_awaited()


@pytest.mark.parametrize("user_id", [ACTOR, BOT, TARGET])
async def test_wrong_member_identity_is_not_accepted(rest_snapshot, user_id):
    state = rest_snapshot
    state.members[user_id]["user"]["id"] = str(user_id + 1)
    with pytest.raises(ModerationError):
        await state.gateway.load(GUILD, ACTOR, TARGET)
    state.kick.assert_not_awaited()


async def test_rest_preflight_failure_is_safe_and_does_not_delete(rest_snapshot):
    state = rest_snapshot
    state.get_guild.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "untrusted API payload")
    with pytest.raises(ModerationError) as error:
        await state.gateway.load(GUILD, ACTOR, TARGET)
    assert "untrusted API payload" not in str(error.value)
    state.kick.assert_not_awaited()


async def test_not_ready_client_is_refused_before_rest(rest_snapshot):
    state = rest_snapshot
    state.client._connection.user = None
    with pytest.raises(ModerationError):
        await state.gateway.load(GUILD, ACTOR, TARGET)
    state.get_guild.assert_not_awaited()
    state.kick.assert_not_awaited()


async def test_kick_uses_detached_context_and_preserves_audit_reason(rest_snapshot):
    state = rest_snapshot
    context = await state.gateway.load(GUILD, ACTOR, TARGET)
    audit_reason = "case: unit-test · actor: 100 · Перевірена причина"
    await state.gateway.kick(context, TARGET, audit_reason)
    state.kick.assert_awaited_once_with(TARGET, GUILD, reason=audit_reason)


def history_row(**changes):
    row = {"id": "a" * 32, "user_id": TARGET, "actor_id": ACTOR, "action": "kick",
           "status": "pending", "created": 1_750_000_000.0, "updated": 1_750_000_001.0,
           "reason": "Тестова причина"}
    row.update(changes)
    return row


def test_history_embed_bounds_worst_case_markdown_and_number_of_rows():
    from core_v2.discord_moderation import history_embed
    rows = [history_row(reason="*" * 500) for _ in range(12)]
    embed = history_embed(rows, page=1000)
    assert len(embed.fields) == 5
    assert len(embed) <= 6000
    assert all(len(field.name) <= 256 and len(field.value) <= 1024 for field in embed.fields)
    assert "Непідтверджено" in embed.fields[0].value
    assert "автоматичного повтору" in embed.description


def test_history_embed_escapes_reason_mentions_and_markdown():
    from core_v2.discord_moderation import history_embed
    reason = "@everyone @here <@123456789012345678> **причина**"
    embed = history_embed([history_row(reason=reason)], page=1)
    rendered = embed.fields[0].value
    assert "@everyone" not in rendered and "@here" not in rendered
    assert "<@123456789012345678>" not in rendered
    assert "**причина**" not in rendered
    assert "причина" in rendered


@pytest.mark.parametrize("created,updated", [(None, None), (float("nan"), float("inf")), (0, -1)])
def test_history_unknown_timestamps_are_reported_as_unknown(created, updated):
    from core_v2.discord_moderation import history_embed
    embed = history_embed([history_row(created=created, updated=updated)], page=1)
    rendered = embed.fields[0].value
    assert rendered.count("Невідомо") == 2
    assert "<t:" not in rendered


def test_empty_history_explains_absent_results_without_claiming_api_state():
    from core_v2.discord_moderation import history_embed
    embed = history_embed([], page=2)
    assert embed.fields == []
    assert "немає" in embed.description
    assert "2" in embed.title


async def test_failed_member_fetch_cancels_and_joins_sibling_reads(rest_snapshot):
    import asyncio
    state = rest_snapshot
    started = {identifier: asyncio.Event() for identifier in (BOT, TARGET)}
    cancelled = set()

    async def fetch(guild_id, identifier):
        if identifier == ACTOR:
            await asyncio.gather(*(event.wait() for event in started.values()))
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "No member access")
        started[identifier].set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.add(identifier)
            raise

    state.get_member.side_effect = fetch
    with pytest.raises(ModerationError):
        await asyncio.wait_for(state.gateway.load(GUILD, ACTOR, TARGET), timeout=2)
    assert cancelled == {BOT, TARGET}
    state.kick.assert_not_awaited()
