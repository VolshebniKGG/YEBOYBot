"""Persistent panels with real SQLite/Discord models and mocked Discord transport."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core_v2.bot import Bot
from core_v2.config import Settings
from core_v2.db import Database
from core_v2 import role_panels as module
from core_v2.role_panels import RoleButton, RolePanels, panel_view
from core_v2.security import DANGEROUS, UserError
from test_discord_moderation import user_payload, role_payload, member_payload

GUILD, ADMIN, BOT, USER, OWNER, CHANNEL = 10, 100, 200, 300, 900, 400
ADMIN_ROLE, BOT_ROLE, ROLE = 101, 201, 301


def request(f, user=ADMIN):
    done = [False]

    async def defer(**kwargs):
        done[0] = True

    member = discord.Member(state=f.bot._connection, guild=f.cached, data=f.members[user])
    return SimpleNamespace(guild=f.cached, guild_id=GUILD, user=member, client=f.bot,
        channel_id=CHANNEL,
        message=SimpleNamespace(author=f.bot.user, guild=f.cached, channel=SimpleNamespace(id=CHANNEL)),
        response=SimpleNamespace(defer=AsyncMock(side_effect=defer), is_done=lambda: done[0],
                                 send_message=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))


@pytest.fixture
async def context(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'panels.db'}"
    bot = Bot(Settings('synthetic', url))
    bot._connection.user = discord.ClientUser(state=bot._connection, data=user_payload(BOT, bot=True))
    roles = [role_payload(GUILD, 0, discord.Permissions(view_channel=True, send_messages=True).value),
             role_payload(ADMIN_ROLE, 10, discord.Permissions(manage_guild=True, manage_roles=True).value),
             role_payload(BOT_ROLE, 20, discord.Permissions(manage_roles=True).value), role_payload(ROLE, 5)]
    guild = {'id': str(GUILD), 'name': 'Fixture', 'owner_id': str(OWNER), 'roles': roles}
    cached = discord.Guild(state=bot._connection, data=deepcopy(guild))
    bot._connection._guilds[GUILD] = cached
    members = {ADMIN: member_payload(ADMIN, [ADMIN_ROLE]), BOT: member_payload(BOT, [BOT_ROLE]),
               USER: member_payload(USER)}
    room = {'id': str(CHANNEL), 'guild_id': str(GUILD), 'name': 'roles', 'type': 0,
            'position': 0, 'permission_overwrites': []}
    get_guild = AsyncMock(side_effect=lambda *a, **k: deepcopy(guild))
    monkeypatch.setattr(bot.http, 'get_guild', get_guild)
    monkeypatch.setattr(bot.http, 'get_roles', AsyncMock(side_effect=lambda *a, **k: deepcopy(roles)))
    monkeypatch.setattr(bot.http, 'get_member', AsyncMock(side_effect=lambda g, u: deepcopy(members[u])))
    monkeypatch.setattr(bot.http, 'get_channel', AsyncMock(side_effect=lambda *a: deepcopy(room)))
    monkeypatch.setattr(bot.http, 'get_all_guild_channels', AsyncMock(side_effect=lambda *a: [deepcopy(room)]))
    send = AsyncMock()
    monkeypatch.setattr(discord.TextChannel, 'send', send)

    async def add(guild_id, user_id, role_id, **kwargs):
        assert (guild_id, user_id, role_id) == (GUILD, USER, ROLE)
        members[user_id]['roles'].append(str(role_id))

    async def remove(guild_id, user_id, role_id, **kwargs):
        assert (guild_id, user_id, role_id) == (GUILD, USER, ROLE)
        members[user_id]['roles'].remove(str(role_id))

    add_role, remove_role = AsyncMock(side_effect=add), AsyncMock(side_effect=remove)
    monkeypatch.setattr(bot.http, 'add_role', add_role)
    monkeypatch.setattr(bot.http, 'remove_role', remove_role)
    clock = SimpleNamespace(value=100.0)
    cog = RolePanels(bot, clock=lambda: clock.value)
    await bot.db.initialize()
    await bot.add_cog(cog)
    f = SimpleNamespace(bot=bot, cog=cog, cached=cached, guild=guild, roles=roles,
        members=members, room=room, send=send, add=add_role, remove=remove_role,
        clock=clock, get_guild=get_guild, url=url)
    try:
        yield f
    finally:
        await bot.remove_cog('RolePanels')
        await bot.db.close()


async def create(f):
    interaction = request(f)
    channel = discord.TextChannel(state=f.bot._connection, guild=f.cached, data=f.room)
    await RolePanels.create.callback(f.cog, interaction, channel, f.cached.get_role(ROLE))
    return (await f.bot.db.list_panels(GUILD))[0]['id'], interaction


def reply(interaction):
    calls = interaction.followup.send.await_args_list or interaction.response.send_message.await_args_list
    assert calls
    for call in calls:
        assert call.kwargs['ephemeral'] is True
        assert call.kwargs['allowed_mentions'].to_dict()['parse'] == []
    return calls[-1].args[0]


async def test_create_persists_before_post_and_registers_persistent_buttons(context):
    f = context

    async def send(content, **kwargs):
        rows = await f.bot.db.list_panels(GUILD)
        assert len(rows) == 1 and rows[0]['id'] in content
        view = kwargs['view']
        assert view.is_persistent() and len(view.children) == 2
        assert kwargs['allowed_mentions'].to_dict()['parse'] == []
        assert {item.action for item in view.children} == {'give', 'remove'}

    f.send.side_effect = send
    panel_id, interaction = await create(f)
    assert panel_id in reply(interaction)
    assert f.bot.tree.get_command('role-panel') is not None
    assert f.cached.get_role(ROLE).position == 5
    assert f.bot.intents.members is False


async def test_buttons_work_after_database_reopen_and_cog_reload(context):
    f = context
    panel_id, _ = await create(f)
    await f.bot.remove_cog('RolePanels')
    await f.bot.db.close()
    f.bot.db = Database(f.url)
    await f.bot.db.initialize()
    f.cog = RolePanels(f.bot, clock=lambda: f.clock.value)
    await f.bot.add_cog(f.cog)
    original = panel_view(panel_id).children[0]
    match = RoleButton.__discord_ui_compiled_template__.fullmatch(original.item.custom_id)
    rebuilt = await RoleButton.from_custom_id(request(f, USER), original.item, match)
    interaction = request(f, USER)
    await rebuilt.callback(interaction)
    assert 'отримано' in reply(interaction)
    f.add.assert_awaited_once()


async def test_give_and_remove_are_idempotent_using_current_member_roles(context):
    f = context
    panel_id, _ = await create(f)
    for action in ('give', 'give', 'remove', 'remove'):
        f.clock.value += 3
        interaction = request(f, USER)
        await f.cog.click(interaction, panel_id, action)
        reply(interaction)
    f.add.assert_awaited_once()
    f.remove.assert_awaited_once()
    assert f.members[USER]['roles'] == []


@pytest.mark.parametrize('permission', ['manage_guild', 'manage_roles'])
async def test_fresh_permission_loss_blocks_create_list_and_remove(context, permission):
    f = context
    panel_id, _ = await create(f)
    flags = discord.Permissions(manage_guild=True, manage_roles=True)
    setattr(flags, permission, False)
    f.roles[1]['permissions'] = str(flags.value)
    for operation in ('create', 'list_panels', 'remove'):
        interaction = request(f)
        with pytest.raises(UserError, match='Manage Guild'):
            if operation == 'create':
                await create(f)
            else:
                await getattr(RolePanels, operation).callback(f.cog, interaction, *([panel_id] if operation == 'remove' else []))
    assert await f.bot.db.panel(GUILD, panel_id) == ROLE


@pytest.mark.parametrize('name', sorted(DANGEROUS))
async def test_changed_privileged_role_never_granted(context, name):
    f = context
    panel_id, _ = await create(f)
    flags = discord.Permissions.none()
    setattr(flags, name, True)
    f.roles[3]['permissions'] = str(flags.value)
    interaction = request(f, USER)
    await f.cog.click(interaction, panel_id, 'give')
    assert 'привілейованих' in reply(interaction)
    f.add.assert_not_awaited()


async def test_channel_overwrite_privileges_block_grant(context):
    f = context
    panel_id, _ = await create(f)
    f.room['permission_overwrites'] = [{'id': str(ROLE), 'type': 0,
        'allow': str(discord.Permissions(manage_messages=True).value), 'deny': '0'}]
    interaction = request(f, USER)
    await f.cog.click(interaction, panel_id, 'give')
    assert 'привілейовані дозволи' in reply(interaction)
    f.add.assert_not_awaited()


@pytest.mark.parametrize('case', ['pending', 'missing_role', 'higher_role', 'managed', 'unknown_member_role', 'no_bot_permission', 'no_channel_view'])
async def test_changed_server_state_blocks_grant(context, case):
    f = context
    panel_id, _ = await create(f)
    if case == 'pending':
        f.members[USER]['pending'] = True
    elif case == 'missing_role':
        f.roles.pop()
    elif case == 'higher_role':
        f.roles[3]['position'] = 30
    elif case == 'managed':
        f.roles[3]['managed'] = True
    elif case == 'unknown_member_role':
        f.members[USER]['roles'] = ['123456']
    elif case == 'no_bot_permission':
        f.roles[2]['permissions'] = '0'
    else:
        f.room['permission_overwrites'] = [{'id': str(USER), 'type': 1, 'allow': '0',
            'deny': str(discord.Permissions(view_channel=True).value)}]
    interaction = request(f, USER)
    await f.cog.click(interaction, panel_id, 'give')
    assert 'отримано' not in reply(interaction)
    f.add.assert_not_awaited()


@pytest.mark.parametrize('case', ['other_bot', 'other_guild_message', 'other_channel', 'foreign_panel', 'dm'])
async def test_scope_and_authorship_blocks_forged_or_foreign_buttons(context, case):
    f = context
    panel_id, _ = await create(f)
    interaction = request(f, USER)
    if case == 'other_bot':
        interaction.message.author = SimpleNamespace(id=BOT+1)
    elif case == 'other_guild_message':
        interaction.message.guild = SimpleNamespace(id=GUILD+1)
    elif case == 'other_channel':
        interaction.channel_id += 1
    elif case == 'foreign_panel':
        panel_id = await f.bot.db.create_panel(GUILD+1, ROLE)
    else:
        interaction.guild = None
    await f.cog.click(interaction, panel_id, 'give')
    reply(interaction)
    f.add.assert_not_awaited()


async def test_revocation_survives_new_cog_and_does_not_remove_existing_roles(context):
    f = context
    panel_id, _ = await create(f)
    f.members[USER]['roles'] = [str(ROLE)]
    interaction = request(f)
    await RolePanels.remove.callback(f.cog, interaction, panel_id)
    assert 'відкликано' in reply(interaction)
    await f.bot.remove_cog('RolePanels')
    f.cog = RolePanels(f.bot)
    await f.bot.add_cog(f.cog)
    interaction = request(f, USER)
    await f.cog.click(interaction, panel_id, 'give')
    assert 'відкликано' in reply(interaction)
    f.add.assert_not_awaited()
    f.remove.assert_not_awaited()


@pytest.mark.parametrize('ambiguous', [False, True])
async def test_failed_send_removes_only_known_rejected_panel(context, ambiguous):
    f = context
    response = SimpleNamespace(status=500 if ambiguous else 403, reason='Fixture')
    kind = discord.HTTPException if ambiguous else discord.Forbidden
    f.send.side_effect = kind(response, 'synthetic-private-details')
    with pytest.raises(UserError) as error:
        await create(f)
    assert 'synthetic-private' not in str(error.value)
    assert len(await f.bot.db.list_panels(GUILD)) == int(ambiguous)


async def test_uncertain_mutation_has_private_generic_response(context):
    f = context
    panel_id, _ = await create(f)
    f.add.side_effect = TimeoutError('private network details')
    interaction = request(f, USER)
    await f.cog.click(interaction, panel_id, 'give')
    text = reply(interaction)
    assert 'не підтвердив' in text and 'private' not in text
    assert await f.bot.db.panel(GUILD, panel_id) == ROLE


async def test_busy_requests_do_not_create_waiters_and_release_after_cancel(context, monkeypatch):
    f = context
    entered = asyncio.Event()

    async def blocked():
        async with f.cog._request(GUILD):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(blocked())
    await entered.wait()
    with pytest.raises(UserError):
        async with f.cog._request(GUILD):
            pytest.fail('busy guild entered')
    monkeypatch.setattr(module, 'MAX_ACTIVE', 1)
    with pytest.raises(UserError):
        async with f.cog._request(GUILD+1):
            pytest.fail('limit exceeded')
    await f.cog.cog_unload()
    assert task.cancelled() and not f.cog.active and not f.cog.tasks


async def test_button_cooldown_blocks_rest_and_state_is_bounded(context, monkeypatch):
    f = context
    panel_id, _ = await create(f)
    await f.cog.click(request(f, USER), panel_id, 'give')
    f.get_guild.reset_mock()
    interaction = request(f, USER)
    await f.cog.click(interaction, panel_id, 'give')
    assert 'дві секунди' in reply(interaction)
    f.get_guild.assert_not_awaited()
    monkeypatch.setattr(module, 'MAX_COOLDOWNS', 2)
    for user_id in (500, 501, 502):
        f.members[user_id] = member_payload(user_id)
        await f.cog.click(request(f, user_id), 'a'*32, 'give')
    assert len(f.cog.cooldowns) == 2


async def test_list_is_private_guild_scoped_and_remove_requires_valid_id(context):
    f = context
    panel_id, _ = await create(f)
    foreign = await f.bot.db.create_panel(GUILD+1, ROLE)
    interaction = request(f)
    await RolePanels.list_panels.callback(f.cog, interaction)
    text = reply(interaction)
    assert panel_id in text and foreign not in text
    with pytest.raises(UserError):
        await RolePanels.remove.callback(f.cog, request(f), 'invalid')


async def test_create_at_cap_shows_actionable_error_without_posting(context):
    f = context
    for _ in range(50):
        await f.bot.db.create_panel(GUILD, ROLE)
    with pytest.raises(UserError, match='50 активних панелей'):
        await create(f)
    f.send.assert_not_awaited()


async def test_actual_discord_view_store_dispatches_existing_message_after_reload(context):
    f = context
    panel_id, _ = await create(f)
    components = panel_view(panel_id).to_components()
    await f.bot.remove_cog('RolePanels')
    await f.bot.db.close()
    f.bot.db = Database(f.url)
    await f.bot.db.initialize()
    f.cog = RolePanels(f.bot, clock=lambda: f.clock.value)
    await f.bot.add_cog(f.cog)
    room = discord.TextChannel(state=f.bot._connection, guild=f.cached, data=f.room)
    message = discord.Message(state=f.bot._connection, channel=room, data={
        'id': '100000000000000001', 'type': 0, 'content': 'Existing panel',
        'author': user_payload(BOT, bot=True), 'attachments': [], 'embeds': [],
        'components': components, 'timestamp': '2026-10-30T12:00:00+00:00',
        'edited_timestamp': None, 'flags': 0, 'tts': False,
        'mention_everyone': False, 'mentions': [], 'pinned': False})
    interaction = request(f, USER)
    interaction.message = message
    custom_id = f'yeboy:role:{panel_id}:give'
    interaction.data = {'component_type': 2, 'custom_id': custom_id}
    store = f.bot._connection._view_store
    store.dispatch_dynamic_items(2, custom_id, interaction)
    pending = tuple(store._ViewStore__tasks)
    assert len(pending) == 1
    await asyncio.gather(*pending)
    f.add.assert_awaited_once()
    assert 'отримано' in reply(interaction)
