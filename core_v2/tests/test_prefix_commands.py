"""Prefix routing uses the actual SDK command transformers; no Discord login."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord import app_commands
import pytest

from core_v2.prefix_commands import PrefixCommands, parse_arguments, resolve_command, namespace_for
from core_v2.security import UserError
from core_v2.commands import Core
from core_v2.guide import Guide
import test_role_panels as panels
from test_role_panels import request, ADMIN, USER, GUILD, CHANNEL, ROLE

panel_context = panels.context


@pytest.fixture
async def fixture(panel_context):
    f = panel_context
    f.prefix = PrefixCommands(f.bot, clock=lambda: f.clock.value)
    await f.bot.add_cog(f.prefix)
    await f.bot.add_cog(Core(f.bot))
    f.bot.tree.on_error = AsyncMock()
    f.bot.tree.interaction_check = AsyncMock(return_value=True)
    try:
        yield f
    finally:
        await f.bot.remove_cog('PrefixCommands')
        await f.bot.remove_cog('Core')


def message(f, content, user=ADMIN):
    interaction = request(f, user)
    return SimpleNamespace(content=content, guild=f.cached, author=interaction.user,
        webhook_id=None, channel=SimpleNamespace(id=CHANNEL, send=f.send))


def interaction(f, user=ADMIN):
    result = request(f, user)
    result.permissions = discord.Permissions.all()
    return result


async def test_real_command_invocation_preserves_ephemeral_and_single_use(fixture):
    f = fixture
    callback = AsyncMock()
    async def probe(interaction: discord.Interaction, number: app_commands.Range[int, 1, 10], text: str):
        await callback(number, text)
        await interaction.response.send_message('private-result', ephemeral=True)
    cmd = app_commands.Command(name='probe', description='test', callback=probe)
    f.bot.tree.add_command(cmd)
    assert await f.prefix.handle_message(message(f, '!probe 3 private words'))
    view = f.send.call_args.kwargs['view']
    click = interaction(f)
    await view.execute.callback(click)
    callback.assert_awaited_once_with(3, 'private words')
    click.response.send_message.assert_awaited_once_with('private-result', ephemeral=True)
    await view.execute.callback(interaction(f))
    callback.assert_awaited_once()
    assert not f.prefix.pending


async def test_actual_sdk_member_role_channel_transformers(fixture):
    f = fixture
    callback = AsyncMock()
    async def probe(interaction: discord.Interaction, member: discord.Member,
                    role: discord.Role, channel: discord.TextChannel,
                    choice: app_commands.Choice[str]):
        await callback(member, role, channel, choice)
    probe = app_commands.choices(choice=[app_commands.Choice(name='Text', value='text')])(probe)
    command = app_commands.Command(name='probe', description='test', callback=probe)
    values = parse_arguments(command, [str(USER), str(ROLE), str(CHANNEL), 'text'])
    namespace = await namespace_for(command, values, f.cached)
    await command._invoke_with_namespace(interaction(f), namespace)
    member, role, channel, choice = callback.await_args.args
    assert isinstance(member, discord.Member) and member.id == USER
    assert isinstance(role, discord.Role) and role.id == ROLE
    assert isinstance(channel, discord.TextChannel) and channel.id == CHANNEL
    assert isinstance(choice, app_commands.Choice) and choice.value == 'text'


@pytest.mark.parametrize('words', [['0','x'], ['11','x'], ['x','x'], ['1'], ['1','']])
def test_range_and_missing_validation(words):
    async def probe(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 10],
                    text: app_commands.Range[str, 1, 10]):
        pass
    command = app_commands.Command(name='probe', description='test', callback=probe)
    with pytest.raises(UserError):
        parse_arguments(command, words)


async def test_optional_skip_and_choices(fixture):
    command = fixture.bot.tree.get_command('leaderboard')
    assert parse_arguments(command, ['-', 'voice']) == {'xp_type': 'voice'}
    with pytest.raises(UserError):
        parse_arguments(command, ['1', 'invalid'])
    levels = fixture.bot.tree.get_command('levels')
    assert parse_arguments(levels, ['так']) == {'enabled': True}
    with pytest.raises(UserError):
        parse_arguments(levels, ['yes'])


async def test_group_and_alias_resolve_same_leaf(fixture):
    f = fixture
    command, args = resolve_command(f.bot.tree, ['role-panel', 'list', '2'], GUILD)
    assert command.qualified_name == 'role-panel list' and args == ['2']
    command, args = resolve_command(f.bot.tree, ['top', '3'], GUILD)
    assert command.name == 'leaderboard' and args == ['3']
    with pytest.raises(UserError):
        resolve_command(f.bot.tree, ['play', 'song'], GUILD)


async def test_default_permissions_checked_before_public_prompt(fixture):
    f = fixture
    await f.prefix.handle_message(message(f, '!levels false', USER))
    assert not f.prefix.pending
    assert 'view' not in f.send.call_args.kwargs
    assert 'Недостатньо' in f.send.call_args.args[0]


@pytest.mark.parametrize('field,value', [('guild_id', 99), ('channel_id', 99)])
async def test_foreign_scope_does_not_consume_button(fixture, field, value):
    f = fixture
    await f.prefix.handle_message(message(f, '!status'))
    view = next(iter(f.prefix.pending))
    click = interaction(f)
    setattr(click, field, value)
    await view.execute.callback(click)
    assert not view.used and view in f.prefix.pending
    f.bot.tree.interaction_check.assert_not_awaited()


async def test_foreign_user_and_channel_permission_denied(fixture):
    f = fixture
    await f.prefix.handle_message(message(f, '!status'))
    view = next(iter(f.prefix.pending))
    await view.execute.callback(interaction(f, USER))
    assert not view.used
    click = interaction(f)
    click.permissions = discord.Permissions(view_channel=True, send_messages=True)
    await view.execute.callback(click)
    assert isinstance(f.bot.tree.on_error.await_args.args[1], UserError)
    f.bot.tree.interaction_check.assert_not_awaited()


async def test_expired_and_removed_commands_never_invoke(fixture):
    f = fixture
    await f.prefix.handle_message(message(f, '!status'))
    view = next(iter(f.prefix.pending))
    f.clock.value += 121
    await view.execute.callback(interaction(f))
    f.bot.tree.interaction_check.assert_not_awaited()
    await view.on_timeout()
    assert not f.prefix.pending
    await f.prefix.handle_message(message(f, '!status'))
    view = next(iter(f.prefix.pending))
    f.bot.tree.remove_command('status')
    await view.execute.callback(interaction(f))
    f.bot.tree.interaction_check.assert_not_awaited()


async def test_cooldown_capacity_and_shutdown(fixture, monkeypatch):
    from core_v2 import prefix_commands as module
    f = fixture
    monkeypatch.setattr(module, 'MAX_PENDING', 1)
    await f.prefix.handle_message(message(f, '!status'))
    await f.prefix.handle_message(message(f, '!status'))
    f.send.assert_awaited_once()
    f.clock.value += 4
    await f.prefix.handle_message(message(f, '!status'))
    f.send.assert_awaited_once()
    await f.prefix.cog_unload()
    assert not f.prefix.pending
    f.clock.value += 4
    await f.prefix.handle_message(message(f, '!status'))
    f.send.assert_awaited_once()


async def test_failed_send_releases_reservation(fixture):
    f = fixture
    f.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='blocked'), 'blocked')
    await f.prefix.handle_message(message(f, '!status'))
    assert not f.prefix.pending and not f.prefix.tasks


async def test_pending_sends_bounded_and_cancelled_on_shutdown(fixture, monkeypatch):
    from core_v2 import prefix_commands as module
    f = fixture
    entered = asyncio.Event()
    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    f.send.side_effect = blocked
    monkeypatch.setattr(module, 'MAX_PENDING', 1)
    task = asyncio.create_task(f.prefix.handle_message(message(f, '!bad-command')))
    await entered.wait()
    f.clock.value += 4
    await f.prefix.handle_message(message(f, '!other-bad-command'))
    f.send.assert_awaited_once()
    await f.prefix.cog_unload()
    assert task.cancelled() and not f.prefix.tasks


async def test_malformed_quotes_and_mentions_do_not_echo_content(fixture):
    f = fixture
    await f.prefix.handle_message(message(f, '!remind "private-secret'))
    assert 'private-secret' not in f.send.call_args.args[0]
    assert f.send.call_args.kwargs['allowed_mentions'].to_dict()['parse'] == []


@pytest.mark.parametrize('text', ["don't stop", "П'яний", '"two words"'])
async def test_natural_apostrophes_and_double_quotes(fixture, text):
    f = fixture
    received = AsyncMock()
    async def probe(interaction: discord.Interaction, query: str):
        await received(query)
    f.bot.tree.add_command(app_commands.Command(name='probe', description='test', callback=probe))
    await f.prefix.handle_message(message(f, '!probe ' + text))
    view = next(iter(f.prefix.pending))
    await view.execute.callback(interaction(f))
    received.assert_awaited_once_with(text.strip('"'))


async def test_real_component_interaction_is_passed_to_slash_handler(fixture, monkeypatch):
    from copy import deepcopy
    f = fixture
    delivered = AsyncMock()
    monkeypatch.setattr(discord.InteractionResponse, 'send_message', delivered)
    seen = []
    async def probe(interaction: discord.Interaction):
        seen.append(interaction)
        await interaction.response.send_message('private', ephemeral=True)
    f.bot.tree.add_command(app_commands.Command(name='probe', description='test', callback=probe))
    await f.prefix.handle_message(message(f, '!probe'))
    view = next(iter(f.prefix.pending))
    member = deepcopy(f.members[ADMIN])
    member['permissions'] = str(discord.Permissions.all().value)
    payload = {'id': '123456789', 'type': 3, 'token': 'synthetic', 'version': 1,
        'application_id': '200', 'guild_id': str(GUILD), 'channel': deepcopy(f.room),
        'attachment_size_limit': 1000000, 'member': member,
        'data': {'component_type': 2, 'custom_id': view.execute.custom_id}}
    click = discord.Interaction(data=payload, state=f.bot._connection)
    await view.execute.callback(click)
    assert seen == [click]
    delivered.assert_awaited_once_with('private', ephemeral=True)
    f.bot.tree.on_error.assert_not_awaited()

async def test_read_only_help_goes_to_private_dm_without_click(fixture, monkeypatch):
    f = fixture
    await f.bot.add_cog(Guide(f.bot))
    dm = AsyncMock()
    monkeypatch.setattr(discord.Member, 'send', dm)
    monkeypatch.setattr(discord.TextChannel, 'permissions_for',
                        lambda self, member: discord.Permissions(
                            view_channel=True, send_messages=True, use_application_commands=True))
    request_message = message(f, '!help music')
    request_message.channel = discord.TextChannel(state=f.bot._connection, guild=f.cached, data=f.room)
    try:
        assert await f.prefix.handle_message(request_message)
        dm.assert_awaited_once()
        kwargs = dm.await_args.kwargs
        assert 'embed' in kwargs and 'Музика' in str(kwargs['embed'].to_dict())
        assert kwargs['allowed_mentions'].to_dict()['parse'] == []
        f.send.assert_not_awaited()
        assert not f.prefix.pending
    finally:
        await f.bot.remove_cog('Guide')


async def test_modules_direct_dm_preserves_private_status(fixture, monkeypatch):
    from core_v2 import prefix_commands as module
    f = fixture
    await f.bot.add_cog(Guide(f.bot))
    dm = AsyncMock()
    monkeypatch.setattr(discord.Member, 'send', dm)
    monkeypatch.setattr(discord.TextChannel, 'permissions_for',
                        lambda self, member: discord.Permissions(
                            view_channel=True, send_messages=True, use_application_commands=True))
    report = AsyncMock(return_value=discord.Embed(title='Private modules'))
    monkeypatch.setattr(module, 'collect_module_status', report)
    request_message = message(f, '!modules')
    request_message.channel = discord.TextChannel(state=f.bot._connection, guild=f.cached, data=f.room)
    try:
        assert await f.prefix.handle_message(request_message)
        report.assert_awaited_once_with(f.bot, f.cached)
        assert dm.await_args.kwargs['embed'].title == 'Private modules'
        assert not f.prefix.pending
        f.send.assert_not_awaited()
    finally:
        await f.bot.remove_cog('Guide')


async def test_blocked_dm_falls_back_to_private_button(fixture, monkeypatch):
    f = fixture
    await f.bot.add_cog(Guide(f.bot))
    dm = AsyncMock(side_effect=discord.Forbidden(
        SimpleNamespace(status=403, reason='DM blocked'), 'DM blocked'))
    monkeypatch.setattr(discord.Member, 'send', dm)
    monkeypatch.setattr(discord.TextChannel, 'permissions_for',
                        lambda self, member: discord.Permissions(
                            view_channel=True, send_messages=True, use_application_commands=True))
    request_message = message(f, '!help')
    request_message.channel = discord.TextChannel(state=f.bot._connection, guild=f.cached, data=f.room)
    try:
        assert await f.prefix.handle_message(request_message)
        dm.assert_awaited_once()
        assert len(f.prefix.pending) == 1
        assert f.send.await_args.kwargs.get('view') is next(iter(f.prefix.pending))
        assert 'embed' not in f.send.await_args.kwargs
        assert f.send.await_args.kwargs['allowed_mentions'].to_dict()['parse'] == []
    finally:
        await f.bot.remove_cog('Guide')


async def test_direct_private_rejects_missing_channel_rights_before_dm(fixture, monkeypatch):
    f = fixture
    await f.bot.add_cog(Guide(f.bot))
    dm = AsyncMock()
    monkeypatch.setattr(discord.Member, 'send', dm)
    monkeypatch.setattr(discord.TextChannel, 'permissions_for',
                        lambda self, member: discord.Permissions(view_channel=True, send_messages=True))
    request_message = message(f, '!help')
    request_message.channel = discord.TextChannel(state=f.bot._connection, guild=f.cached, data=f.room)
    try:
        assert await f.prefix.handle_message(request_message)
        dm.assert_not_awaited()
        assert 'view' not in f.send.await_args.kwargs
        assert 'Use Application Commands' in f.send.await_args.args[0]
        assert not f.prefix.pending
    finally:
        await f.bot.remove_cog('Guide')
