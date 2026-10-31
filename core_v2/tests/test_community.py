"""Community configuration and event safety, without contacting Discord."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core_v2.community import Community, MAX_EVENTS, validate_autorole
from core_v2.security import DANGEROUS, UserError


class Role:
    def __init__(self, guild, position=1, **permissions):
        self.guild = guild
        self.id = 30
        self.position = position
        self.managed = False
        self.permissions = discord.Permissions(**permissions)

    def is_default(self):
        return self.id == self.guild.id

    def __ge__(self, other):
        return self.position >= other.position


@pytest.fixture
def context():
    guild = SimpleNamespace(id=10, owner_id=99, unavailable=False)
    role = Role(guild)
    me = SimpleNamespace(guild_permissions=discord.Permissions(manage_roles=True),
                         top_role=Role(guild, 10))
    guild.me = me
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 50
    channel.guild = guild
    channel.permissions_for.return_value = discord.Permissions(view_channel=True, send_messages=True)
    channel.send = AsyncMock()
    guild.get_channel = lambda channel_id: channel if channel_id == 50 else None
    guild.get_role = lambda role_id: role if role_id == 30 else None
    actor = MagicMock(spec=discord.Member)
    actor.id = 20
    actor.top_role = Role(guild, 5)
    actor.guild_permissions = discord.Permissions(manage_guild=True, manage_roles=True)
    member = SimpleNamespace(id=40, guild=guild, roles=[], bot=False, pending=False, add_roles=AsyncMock())
    settings = dict(guild_id=10, welcome_channel=50, log_channel=50, autorole=30)
    bot = SimpleNamespace(db=SimpleNamespace(configure=AsyncMock(), get_settings=AsyncMock(return_value=settings)),
                          settings_cache={10: object()}, intents=SimpleNamespace(members=True),
                          get_guild=lambda guild_id: guild if guild_id == 10 else None)
    interaction = SimpleNamespace(guild=guild, user=actor, response=SimpleNamespace(defer=AsyncMock()),
                                  followup=SimpleNamespace(send=AsyncMock()))
    return SimpleNamespace(cog=Community(bot), bot=bot, guild=guild, channel=channel,
                           role=role, actor=actor, member=member, interaction=interaction, settings=settings)


@pytest.mark.parametrize('name,value,key', [('welcome', 'channel', 'welcome_channel'),
                                           ('logs', 'channel', 'log_channel'),
                                           ('autorole', 'role', 'autorole')])
async def test_commands_persist_and_invalidate_cache(context, name, value, key):
    c = context
    await getattr(Community, name).callback(c.cog, c.interaction, getattr(c, value))
    c.bot.db.configure.assert_awaited_once_with(10, **{key: getattr(c, value).id})
    assert 10 not in c.bot.settings_cache
    c.interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    assert c.interaction.followup.send.await_args.kwargs['allowed_mentions'].to_dict()['parse'] == []


@pytest.mark.parametrize('name', ['welcome', 'logs', 'autorole'])
async def test_runtime_permission_required(context, name):
    c = context
    c.actor.guild_permissions = discord.Permissions.none()
    with pytest.raises(UserError):
        await getattr(Community, name).callback(c.cog, c.interaction)
    c.bot.db.configure.assert_not_awaited()


async def test_autorole_requires_manage_roles_even_for_manager(context):
    c = context
    c.actor.guild_permissions = discord.Permissions(manage_guild=True)
    with pytest.raises(UserError):
        await Community.autorole.callback(c.cog, c.interaction, c.role)
    c.bot.db.configure.assert_not_awaited()


@pytest.mark.parametrize('name', ['welcome', 'autorole'])
async def test_members_intent_needed_only_for_enable(context, name):
    c = context
    c.bot.intents.members = False
    value = c.channel if name == 'welcome' else c.role
    with pytest.raises(UserError, match='ENABLE_MEMBERS'):
        await getattr(Community, name).callback(c.cog, c.interaction, value)
    await getattr(Community, name).callback(c.cog, c.interaction)
    assert c.bot.db.configure.await_args.kwargs == {('welcome_channel' if name == 'welcome' else 'autorole'): None}


async def test_cross_guild_channel_refused(context):
    c = context
    c.channel.guild = SimpleNamespace(id=11)
    with pytest.raises(UserError):
        await Community.logs.callback(c.cog, c.interaction, c.channel)
    c.bot.db.configure.assert_not_awaited()


@pytest.mark.parametrize('permission', sorted(DANGEROUS))
def test_privileged_autoroles_refused(context, permission):
    c = context
    setattr(c.role.permissions, permission, True)
    with pytest.raises(UserError):
        validate_autorole(c.guild, c.role, c.actor)


@pytest.mark.parametrize('change', ['managed', 'everyone', 'above_actor', 'above_bot', 'foreign'])
def test_role_scope_and_hierarchy(context, change):
    c = context
    if change == 'managed':
        c.role.managed = True
    elif change == 'everyone':
        c.role.id = 10
    elif change == 'above_actor':
        c.role.position = 5
    elif change == 'above_bot':
        c.role.position = 10
    else:
        c.role.guild = SimpleNamespace(id=11)
    with pytest.raises(UserError):
        validate_autorole(c.guild, c.role, c.actor)


async def test_join_assigns_and_sends_without_mentions(context):
    c = context
    await c.cog.on_member_join(c.member)
    c.member.add_roles.assert_awaited_once()
    assert c.channel.send.await_count == 2
    for call in c.channel.send.await_args_list:
        assert call.kwargs['allowed_mentions'].to_dict()['parse'] == []
    assert c.cog._active == 0


@pytest.mark.parametrize('kind', ['pending', 'bot', 'existing', 'privileged', 'missing', 'no_bot_permission'])
async def test_join_does_not_assign_unsafe_or_unnecessary_role(context, kind):
    c = context
    if kind in {'pending', 'bot'}:
        setattr(c.member, kind, True)
    elif kind == 'existing':
        c.member.roles = [c.role]
    elif kind == 'privileged':
        c.role.permissions.administrator = True
    elif kind == 'missing':
        c.guild.get_role = lambda _: None
    else:
        c.guild.me.guild_permissions.manage_roles = False
    await c.cog.on_member_join(c.member)
    c.member.add_roles.assert_not_awaited()


async def test_screening_completion_assigns_only_role(context):
    c = context
    await c.cog.on_member_update(SimpleNamespace(pending=True), c.member)
    c.member.add_roles.assert_awaited_once()
    c.channel.send.assert_not_awaited()


async def test_unavailable_guild_and_failed_db_skip_events(context):
    c = context
    c.guild.unavailable = True
    await c.cog.on_member_join(c.member)
    c.bot.db.get_settings.assert_not_awaited()
    c.guild.unavailable = False
    c.bot.db.get_settings.side_effect = RuntimeError('sensitive details')
    await c.cog.on_member_join(c.member)
    c.member.add_roles.assert_not_awaited()
    c.channel.send.assert_not_awaited()
    assert c.cog._active == 0


async def test_discord_failure_does_not_prevent_notification(context):
    c = context
    c.member.add_roles.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
    await c.cog.on_member_join(c.member)
    assert c.channel.send.await_count == 2


async def test_channel_permission_revocation_prevents_delivery(context):
    c = context
    c.channel.permissions_for.return_value = discord.Permissions.none()
    await c.cog.on_guild_channel_create(SimpleNamespace(guild=c.guild, id=80))
    c.channel.send.assert_not_awaited()


async def test_burst_drops_excess_and_releases_slots(context):
    c = context
    started = asyncio.Event()
    release = asyncio.Event()
    async def blocked_settings(guild_id):
        started.set()
        await release.wait()
        return c.settings
    c.bot.db.get_settings.side_effect = blocked_settings
    tasks = [asyncio.create_task(c.cog.on_member_join(c.member)) for _ in range(MAX_EVENTS)]
    await started.wait()
    await asyncio.sleep(0)
    assert c.cog._active == MAX_EVENTS
    await c.cog.on_member_join(c.member)
    assert c.bot.db.get_settings.await_count == MAX_EVENTS
    release.set()
    await asyncio.gather(*tasks)
    assert c.cog._active == 0


async def test_cancellation_releases_slot(context):
    c = context
    c.bot.db.get_settings.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await c.cog.on_member_join(c.member)
    assert c.cog._active == 0


async def test_raw_departure_logs_uncached_member(context):
    c = context
    await c.cog.on_raw_member_remove(SimpleNamespace(guild_id=10, user=SimpleNamespace(id=123)))
    assert '123' in c.channel.send.await_args.args[0]


@pytest.mark.parametrize('operation', ['settings', 'send', 'configure'])
async def test_unload_cancels_and_joins_busy_operations(context, monkeypatch, operation):
    import core_v2.community as module
    c = context
    monkeypatch.setattr(module, 'SHUTDOWN_GRACE', 0.01)
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    if operation == 'settings':
        c.bot.db.get_settings.side_effect = blocked
        work = c.cog.on_member_join(c.member)
    elif operation == 'send':
        c.channel.send.side_effect = blocked
        work = c.cog.on_guild_role_create(c.role)
    else:
        c.bot.db.configure.side_effect = blocked
        work = Community.welcome.callback(c.cog, c.interaction, c.channel)
    task = asyncio.create_task(work)
    await entered.wait()
    await asyncio.wait_for(c.cog.cog_unload(), timeout=1)
    assert cleaned.is_set()
    assert task.cancelled()
    assert c.cog._closing and c.cog._active == 0 and not c.cog._tasks
    # Already-dispatched and new callbacks cannot touch SQL after shutdown.
    before = c.bot.db.get_settings.await_count
    await c.cog.on_member_join(c.member)
    await c.cog.on_guild_role_delete(c.role)
    assert c.bot.db.get_settings.await_count == before
    await c.cog.cog_unload()  # repeated shutdown is harmless


async def test_unload_allows_short_work_to_finish(context):
    c = context
    entered, release = asyncio.Event(), asyncio.Event()

    async def finish_soon(*args, **kwargs):
        entered.set()
        await release.wait()

    c.channel.send.side_effect = finish_soon
    task = asyncio.create_task(c.cog.on_guild_role_create(c.role))
    await entered.wait()
    closing = asyncio.create_task(c.cog.cog_unload())
    await asyncio.sleep(0)
    assert c.cog._closing and not closing.done()
    release.set()
    await closing
    assert task.done() and not task.cancelled()
    assert c.cog._active == 0 and not c.cog._tasks


async def test_cancelled_unload_still_joins_handlers(context):
    c = context
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    c.bot.db.get_settings.side_effect = blocked
    task = asyncio.create_task(c.cog.on_member_join(c.member))
    await entered.wait()
    closing = asyncio.create_task(c.cog.cog_unload())
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert cleaned.is_set() and task.cancelled()
    assert c.cog._active == 0 and not c.cog._tasks


async def test_unload_never_cancels_or_waits_on_its_current_task(context):
    c = context
    async with c.cog._slot() as admitted:
        assert admitted
        await c.cog.cog_unload()
    assert c.cog._closing and not c.cog._tasks and c.cog._active == 0


async def test_configuration_after_unload_is_refused_without_sql(context):
    c = context
    await c.cog.cog_unload()
    with pytest.raises(UserError, match='завершує'):
        await Community.welcome.callback(c.cog, c.interaction, c.channel)
    c.bot.db.configure.assert_not_awaited()
