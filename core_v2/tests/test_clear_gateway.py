"""Fresh channel permissions and exact clear IDs through real SDK models, offline."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core_v2 import discord_clear as adapter
from core_v2.clear_plan import ClearPlan, request_key
from core_v2.moderation import ModerationError
from test_discord_moderation import (
    ACTOR, ACTOR_ROLE, BOT, BOT_ROLE, GUILD,
    guild_payload, member_payload, user_payload,
)

CHANNEL = 555
NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
REQUEST = discord.utils.time_snowflake(NOW, high=True)
FIRST = discord.utils.time_snowflake(NOW - timedelta(hours=1))
SECOND = FIRST - 1
PERMISSIONS = discord.Permissions(view_channel=True, read_message_history=True, manage_messages=True).value


def message_payload(identifier, *, pinned=False, message_type=0, channel_id=CHANNEL):
    return {"id": str(identifier), "channel_id": str(channel_id),
            "author": user_payload(ACTOR), "content": "", "timestamp": NOW.isoformat(),
            "edited_timestamp": None, "tts": False, "mention_everyone": False,
            "mentions": [], "mention_roles": [], "attachments": [], "embeds": [],
            "pinned": pinned, "type": message_type}


def api_error(status=404, code=10008, kind=discord.NotFound):
    return kind(SimpleNamespace(status=status, reason="offline"),
                {"code": code, "message": "private-REST-payload"})


@pytest.fixture
async def channel_state(monkeypatch):
    client = discord.Client(intents=discord.Intents.none())
    client._connection.user = discord.ClientUser(state=client._connection, data=user_payload(BOT, bot=True))
    fresh = guild_payload(actor_permissions=PERMISSIONS)
    fresh["roles"][2]["permissions"] = str(PERMISSIONS)
    cached = discord.Guild(state=client._connection, data=guild_payload(actor_permissions=0))
    client._connection._guilds[GUILD] = cached
    channel = {"id": str(CHANNEL), "guild_id": str(GUILD), "type": 0, "name": "очищення",
               "position": 0, "permission_overwrites": [], "parent_id": None,
               "last_message_id": None, "topic": None, "nsfw": False}
    members = {ACTOR: member_payload(ACTOR, [ACTOR_ROLE]), BOT: member_payload(BOT, [BOT_ROLE])}
    messages = {FIRST: message_payload(FIRST), SECOND: message_payload(SECOND)}
    get_guild = AsyncMock(side_effect=lambda *args, **kwargs: deepcopy(fresh))
    get_channel = AsyncMock(side_effect=lambda *args: deepcopy(channel))
    get_member = AsyncMock(side_effect=lambda guild_id, user_id: deepcopy(members[user_id]))
    get_message = AsyncMock(side_effect=lambda channel_id, message_id: deepcopy(messages[message_id]))
    logs = AsyncMock(return_value=list(messages.values()))
    single, bulk = AsyncMock(), AsyncMock()
    for name, handler in {"get_guild": get_guild, "get_channel": get_channel,
                          "get_member": get_member, "get_message": get_message,
                          "logs_from": logs, "delete_message": single, "delete_messages": bulk}.items():
        monkeypatch.setattr(client.http, name, handler)
    monkeypatch.setattr(adapter, "_now", lambda: NOW)
    monkeypatch.setattr(discord.utils, "utcnow", lambda: NOW)
    state = SimpleNamespace(client=client, gateway=adapter.ClearGateway(client), fresh=fresh,
                            cached=cached, channel=channel, members=members, messages=messages,
                            get_guild=get_guild, get_channel=get_channel, get_member=get_member,
                            get_message=get_message, logs=logs, single=single, bulk=bulk)
    try:
        yield state
    finally:
        await client.close()


def plan(**changes):
    result = ClearPlan(request_key(GUILD, REQUEST), GUILD, ACTOR, CHANNEL, REQUEST,
                       "Прибрати спам", 2, (FIRST, SECOND), NOW.timestamp() + 60)
    return replace(result, **changes)


async def test_fresh_roles_and_channel_overwrites_control_authority_without_mutating_cache(channel_state):
    state = channel_state
    cached_roles = state.cached._roles.copy()
    await state.gateway.check(GUILD, ACTOR, CHANNEL)
    assert state.client.get_guild(GUILD) is state.cached
    assert state.cached._roles == cached_roles
    assert not state.cached.get_role(ACTOR_ROLE).permissions.manage_messages
    state.get_guild.assert_awaited_once_with(GUILD, with_counts=False)
    state.get_channel.assert_awaited_once_with(CHANNEL)
    assert {call.args for call in state.get_member.await_args_list} == {(GUILD, ACTOR), (GUILD, BOT)}
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()


@pytest.mark.parametrize("identifier", [ACTOR, BOT])
@pytest.mark.parametrize("permission", ["manage_messages", "view_channel", "read_message_history"])
async def test_member_channel_overwrite_denial_blocks_clear(channel_state, identifier, permission):
    state = channel_state
    deny = discord.Permissions(**{permission: True}).value
    state.channel["permission_overwrites"] = [{"id": str(identifier), "type": 1, "allow": "0", "deny": str(deny)}]
    with pytest.raises(ModerationError):
        await state.gateway.check(GUILD, ACTOR, CHANNEL)
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()


async def test_everyone_overwrite_and_owner_administrator_rules_follow_actual_sdk(channel_state):
    state = channel_state
    state.channel["permission_overwrites"] = [{"id": str(GUILD), "type": 0, "allow": "0", "deny": str(PERMISSIONS)}]
    with pytest.raises(ModerationError):
        await state.gateway.check(GUILD, ACTOR, CHANNEL)
    admin = str(discord.Permissions(administrator=True).value)
    state.fresh["roles"][1]["permissions"] = admin
    state.fresh["roles"][2]["permissions"] = admin
    await state.gateway.check(GUILD, ACTOR, CHANNEL)


@pytest.mark.parametrize("identifier", [ACTOR, BOT])
async def test_unknown_raw_roles_fail_closed(channel_state, identifier):
    state = channel_state
    state.members[identifier]["roles"].append("999999")
    with pytest.raises(ModerationError):
        await state.gateway.check(GUILD, ACTOR, CHANNEL)


@pytest.mark.parametrize("change", [{"guild_id": str(GUILD + 1)}, {"id": str(CHANNEL + 1)},
                                    {"type": 1}, {"type": 5}, {"type": 10}, {"type": 11},
                                    {"type": 12}, {"type": 15}, {"type": False}])
async def test_other_channel_identity_or_kind_is_rejected_before_reads(channel_state, change):
    state = channel_state
    state.channel.update(change)
    with pytest.raises(ModerationError):
        await state.gateway.snapshot(GUILD, ACTOR, CHANNEL, REQUEST, 2)
    state.logs.assert_not_awaited()
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()


async def test_missing_client_readiness_blocks_all_http(channel_state):
    state = channel_state
    state.client._connection.user = None
    with pytest.raises(ModerationError):
        await state.gateway.check(GUILD, ACTOR, CHANNEL)
    state.get_guild.assert_not_awaited()
    state.get_channel.assert_not_awaited()


@pytest.mark.parametrize("limit", [0, 26, True, "2", None])
async def test_limit_is_bounded_before_http(channel_state, limit):
    with pytest.raises(ModerationError):
        await channel_state.gateway.snapshot(GUILD, ACTOR, CHANNEL, REQUEST, limit)
    channel_state.get_guild.assert_not_awaited()


async def test_snapshot_uses_one_bounded_before_page_and_skips_without_refilling(channel_state):
    state = channel_state
    old = discord.utils.time_snowflake(NOW - timedelta(days=8))
    pinned, system = FIRST - 2, FIRST - 3
    state.logs.return_value = [message_payload(FIRST), message_payload(pinned, pinned=True),
                              message_payload(old), message_payload(system, message_type=6),
                              message_payload(SECOND, message_type=19)]
    selected = await state.gateway.snapshot(GUILD, ACTOR, CHANNEL, REQUEST, 5)
    assert selected == adapter.Selection((FIRST, SECOND), 1, 1, 1)
    state.logs.assert_awaited_once_with(CHANNEL, 5, before=REQUEST)
    assert state.client.intents.message_content is False
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()


@pytest.mark.parametrize("payloads", [[], [message_payload(FIRST), message_payload(FIRST)],
                                     [message_payload(REQUEST)], [message_payload(FIRST, channel_id=CHANNEL + 1)]],
                         ids=["empty", "duplicate", "after-command", "foreign-channel"])
async def test_snapshot_empty_or_invalid_pages_are_not_silently_reselected(channel_state, payloads):
    state = channel_state
    state.logs.return_value = payloads
    if not payloads:
        assert (await state.gateway.snapshot(GUILD, ACTOR, CHANNEL, REQUEST, 2)).message_ids == ()
    else:
        with pytest.raises(ModerationError):
            await state.gateway.snapshot(GUILD, ACTOR, CHANNEL, REQUEST, 2)
    state.logs.assert_awaited_once()


async def test_snapshot_rejects_unbounded_server_page(channel_state):
    state = channel_state
    with pytest.raises(ModerationError):
        await state.gateway.snapshot(GUILD, ACTOR, CHANNEL, REQUEST, 1)
    state.logs.assert_awaited_once()


@pytest.mark.parametrize("failure", [api_error(), api_error(403, 50013, discord.Forbidden),
                                     api_error(500, 0, discord.HTTPException), RuntimeError("private-REST-payload")],
                         ids=["deleted", "forbidden", "server", "transport"])
async def test_read_errors_are_controlled_and_never_write(channel_state, failure):
    state = channel_state
    state.get_message.side_effect = failure
    with pytest.raises(ModerationError) as error:
        await state.gateway.validate(plan())
    assert "private-REST-payload" not in str(error.value)
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()


async def test_validate_reads_exact_saved_ids_and_no_new_history(channel_state):
    state = channel_state
    await state.gateway.validate(plan())
    assert {call.args for call in state.get_message.await_args_list} == {(CHANNEL, FIRST), (CHANNEL, SECOND)}
    state.logs.assert_not_awaited()
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()


@pytest.mark.parametrize("change", [{"pinned": True}, {"type": 6}, {"id": str(FIRST - 10)},
                                    {"channel_id": str(CHANNEL + 1)}, {"guild_id": str(GUILD + 1)}])
async def test_changed_saved_message_blocks_entire_request_without_skipping(channel_state, change):
    state = channel_state
    state.messages[FIRST].update(change)
    with pytest.raises(ModerationError):
        await state.gateway.validate(plan())
    state.logs.assert_not_awaited()
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()


async def test_validate_rejects_saved_message_that_aged_past_seven_days(channel_state):
    state = channel_state
    old = discord.utils.time_snowflake(NOW - timedelta(days=8))
    state.messages[old] = message_payload(old)
    with pytest.raises(ModerationError):
        await state.gateway.validate(plan(message_ids=(old,), requested=1))
    state.bulk.assert_not_awaited()


async def test_validation_cancels_and_joins_other_message_reads(channel_state):
    state = channel_state
    waiting, cancelled = asyncio.Event(), asyncio.Event()
    async def fetch(channel_id, identifier):
        if identifier == FIRST:
            await waiting.wait()
            raise api_error()
        waiting.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise
    state.get_message.side_effect = fetch
    with pytest.raises(ModerationError):
        await state.gateway.validate(plan())
    assert cancelled.is_set()


async def test_second_authority_read_cancels_and_joins_its_sibling(channel_state):
    state = channel_state
    counts = {ACTOR: 0, BOT: 0}
    waiting, cancelled = asyncio.Event(), asyncio.Event()
    async def fetch(guild_id, identifier):
        counts[identifier] += 1
        if counts[identifier] == 1:
            return deepcopy(state.members[identifier])
        if identifier == ACTOR:
            await waiting.wait()
            raise api_error(403, 50013, discord.Forbidden)
        waiting.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise
    state.get_member.side_effect = fetch
    with pytest.raises(ModerationError):
        await state.gateway.check(GUILD, ACTOR, CHANNEL)
    assert cancelled.is_set()
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()


async def test_future_message_timestamp_is_rejected_even_before_future_request(channel_state):
    state = channel_state
    future = discord.utils.time_snowflake(NOW + timedelta(hours=1))
    request = discord.utils.time_snowflake(NOW + timedelta(days=1))
    state.logs.return_value = [message_payload(future)]
    with pytest.raises(ModerationError):
        await state.gateway.snapshot(GUILD, ACTOR, CHANNEL, request, 1)
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()


@pytest.mark.parametrize("count", [1, 2])
async def test_delete_uses_exact_saved_ids_and_preserves_audit_reason_for_both_sdk_paths(channel_state, count):
    state = channel_state
    selected = plan(message_ids=(FIRST,) if count == 1 else (FIRST, SECOND))
    reason = "clear=offline; actor=100; verified"
    await state.gateway.delete(selected, reason)
    if count == 1:
        state.single.assert_awaited_once_with(CHANNEL, FIRST, reason=reason)
        state.bulk.assert_not_awaited()
    else:
        state.bulk.assert_awaited_once_with(CHANNEL, [FIRST, SECOND], reason=reason)
        state.single.assert_not_awaited()
    state.logs.assert_not_awaited()


async def test_permission_loss_after_validate_is_rechecked_before_mutation(channel_state):
    state = channel_state
    await state.gateway.validate(plan())
    state.channel["permission_overwrites"] = [{"id": str(ACTOR), "type": 1, "allow": "0", "deny": str(PERMISSIONS)}]
    with pytest.raises(ModerationError):
        await state.gateway.delete(plan(), "clear=offline")
    state.single.assert_not_awaited()
    state.bulk.assert_not_awaited()


@pytest.mark.parametrize("failure", [api_error(), api_error(500, 0, discord.HTTPException),
                                     RuntimeError("private-REST-payload"), asyncio.CancelledError()],
                         ids=["write404", "server", "transport", "cancelled"])
async def test_mutation_error_propagates_once_without_application_retry(channel_state, failure):
    state = channel_state
    state.bulk.side_effect = failure
    with pytest.raises(type(failure)):
        await state.gateway.delete(plan(), "clear=offline")
    state.bulk.assert_awaited_once()
    state.logs.assert_not_awaited()


@pytest.mark.parametrize("reason", ["", "\0", "😀" * 500, None], ids=["empty", "nul", "overlong", "missing"])
async def test_bad_audit_reason_never_reaches_deletion(channel_state, reason):
    with pytest.raises(ModerationError):
        await channel_state.gateway.delete(plan(), reason)
    channel_state.single.assert_not_awaited()
    channel_state.bulk.assert_not_awaited()


async def test_current_owner_has_sdk_permission_bypass_but_bot_still_needs_access(channel_state):
    state = channel_state
    state.fresh["owner_id"] = str(ACTOR)
    state.fresh["roles"][1]["permissions"] = "0"
    await state.gateway.check(GUILD, ACTOR, CHANNEL)
    state.fresh["roles"][2]["permissions"] = "0"
    with pytest.raises(ModerationError):
        await state.gateway.check(GUILD, ACTOR, CHANNEL)


async def test_current_member_timeout_removes_clear_authority(channel_state):
    state = channel_state
    state.members[ACTOR]["communication_disabled_until"] = (NOW + timedelta(hours=1)).isoformat()
    with pytest.raises(ModerationError):
        await state.gateway.check(GUILD, ACTOR, CHANNEL)
