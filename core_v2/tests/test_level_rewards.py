"""Cumulative rewards against real SQLite and Discord models, mocked HTTP only."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import discord
from discord import app_commands
import pytest

from core_v2 import level_rewards as module
from core_v2.db import Database
from core_v2.level_rewards import LevelRewards, earned_level
from core_v2.security import DANGEROUS, UserError
import test_role_panels as panels
from test_role_panels import request, reply, GUILD, USER, ROLE, BOT_ROLE, ADMIN_ROLE

panel_context = panels.context


@pytest.fixture
async def context(panel_context):
    f = panel_context
    f.rewards = LevelRewards(f.bot)
    await f.bot.add_cog(f.rewards)
    try:
        yield f
    finally:
        await f.bot.remove_cog('LevelRewards')


async def configure(f, *, kind='text', level=2):
    interaction = request(f)
    await LevelRewards.set_reward.callback(f.rewards, interaction,
        app_commands.Choice(name=kind, value=kind), level, f.cached.get_role(ROLE))
    return interaction


async def earn(f, kind='text', value=100):
    # Use actual durable award cooldown logic, without manipulating SQL tables.
    for index in range(value // 5):
        await f.bot.db.award(GUILD, USER, kind=kind, now=1000 + index * 60)


async def claim(f):
    interaction = request(f, USER)
    await LevelRewards.claim_rewards.callback(f.rewards, interaction)
    return interaction


@pytest.mark.parametrize('kind,multiplier', [('text', 100), ('voice', 50)])
def test_level_thresholds_match_rank_card(kind, multiplier):
    from core_v2.rank_card import level_progress
    for level in (2, 3, 10, 1000):
        threshold = (level - 1)**2 * multiplier
        for value in (threshold - 1, threshold, threshold + 1, 2**63 - 1):
            assert earned_level(value, kind) == level_progress(value, multiplier)[0]


@pytest.mark.parametrize('value', [True, -1, 2**63, 1.5, '100'])
def test_bad_xp_never_used_for_rewards(value):
    with pytest.raises(ValueError):
        earned_level(value, 'text')


async def test_persistent_rule_claim_after_database_and_cog_reload(context):
    f = context
    assert 'збережено' in reply(await configure(f))
    await earn(f)
    await f.bot.remove_cog('LevelRewards')
    await f.bot.db.close()
    f.bot.db = Database(f.url)
    await f.bot.db.initialize()
    f.rewards = LevelRewards(f.bot)
    await f.bot.add_cog(f.rewards)
    assert '1' in reply(await claim(f))
    assert 'Нових' in reply(await claim(f))
    f.add.assert_awaited_once()
    f.remove.assert_not_awaited()
    assert f.bot.tree.get_command('level-reward').default_permissions.manage_roles
    assert f.bot.tree.get_command('claim-rewards').default_permissions is None


async def test_text_and_voice_thresholds_are_independent(context):
    f = context
    await configure(f, kind='voice')
    await earn(f, kind='text', value=1000)
    assert 'Нових' in reply(await claim(f))
    await earn(f, kind='voice', value=45)
    assert 'Нових' in reply(await claim(f))
    await f.bot.db.award(GUILD, USER, kind='voice', now=100000)
    assert '1' in reply(await claim(f))


async def test_same_role_multiple_earned_rules_granted_once(context):
    f = context
    await configure(f)
    await configure(f, kind='voice')
    await earn(f)
    await earn(f, kind='voice', value=50)
    await claim(f)
    f.add.assert_awaited_once()


@pytest.mark.parametrize('name', sorted(DANGEROUS))
async def test_fresh_dangerous_permissions_block_grant(context, name):
    f = context
    await configure(f)
    await earn(f)
    flags = discord.Permissions.none()
    setattr(flags, name, True)
    f.roles[3]['permissions'] = str(flags.value)
    with pytest.raises(UserError, match='привілейованих'):
        await claim(f)
    f.add.assert_not_awaited()


async def test_channel_privilege_escalation_blocks_grant(context):
    f = context
    await configure(f)
    await earn(f)
    f.room['permission_overwrites'] = [{'id': str(ROLE), 'type': 0,
        'allow': str(discord.Permissions(manage_messages=True).value), 'deny': '0'}]
    with pytest.raises(UserError, match='каналів'):
        await claim(f)
    f.add.assert_not_awaited()


@pytest.mark.parametrize('field', ['pending', 'bot'])
async def test_pending_and_bot_members_never_receive_rewards(context, field):
    f = context
    await configure(f)
    await earn(f)
    if field == 'bot':
        f.members[USER]['user']['bot'] = True
    else:
        f.members[USER]['pending'] = True
    with pytest.raises(UserError, match='людям'):
        await claim(f)
    f.add.assert_not_awaited()


@pytest.mark.parametrize('name', ['manage_guild', 'manage_roles'])
async def test_fresh_admin_permission_loss_blocks_all_config_commands(context, name):
    f = context
    await configure(f)
    flags = discord.Permissions(manage_guild=True, manage_roles=True)
    setattr(flags, name, False)
    f.roles[1]['permissions'] = str(flags.value)
    choice = app_commands.Choice(name='Чат', value='text')
    for command, args in [('set_reward', (choice, 2, f.cached.get_role(ROLE))),
                          ('remove_reward', (choice, 2)), ('list_rewards', ())]:
        with pytest.raises(UserError, match='Manage Guild'):
            await getattr(LevelRewards, command).callback(f.rewards, request(f), *args)
    assert len(await f.bot.db.level_rewards(GUILD)) == 1


@pytest.mark.parametrize('role_id', [GUILD, BOT_ROLE, ADMIN_ROLE])
async def test_unsafe_configured_role_rejected(context, role_id):
    f = context
    with pytest.raises(UserError):
        await LevelRewards.set_reward.callback(f.rewards, request(f),
            app_commands.Choice(name='Чат', value='text'), 2, f.cached.get_role(role_id))
    assert not await f.bot.db.level_rewards(GUILD)


async def test_disabled_levels_blocks_award_and_public_claim(context):
    f = context
    await configure(f)
    await earn(f)
    await f.bot.db.configure(GUILD, levels=False)
    with pytest.raises(UserError, match='вимкнена'):
        await claim(f)
    assert f.rewards.enqueue(GUILD, USER)
    await asyncio.gather(*f.rewards.tasks)
    f.add.assert_not_awaited()


async def test_revocation_persists_and_never_removes_existing_roles(context):
    f = context
    await configure(f)
    await earn(f)
    await claim(f)
    interaction = request(f)
    await LevelRewards.remove_reward.callback(f.rewards, interaction,
        app_commands.Choice(name='Чат', value='text'), 2)
    assert 'залишаються' in reply(interaction)
    assert not await f.bot.db.level_rewards(GUILD)
    assert 'Нових' in reply(await claim(f))
    f.remove.assert_not_awaited()


async def test_cross_guild_cached_context_rejected(context):
    f = context
    interaction = request(f)
    interaction.guild_id = 11
    with pytest.raises(UserError, match='цьому серверу'):
        await LevelRewards.list_rewards.callback(f.rewards, interaction)
    f.get_guild.assert_not_awaited()


async def test_guild_removed_during_rest_snapshot_never_grants(context):
    f = context
    await configure(f)
    await earn(f)
    async def rooms(*args):
        f.bot._connection._guilds.pop(GUILD)
        return [deepcopy(f.room)]
    f.bot.http.get_all_guild_channels.side_effect = rooms
    with pytest.raises(UserError, match='недоступний'):
        await claim(f)
    f.add.assert_not_awaited()


async def test_automatic_admission_reserved_before_scheduling(context):
    f = context
    await configure(f)
    await earn(f)
    assert f.rewards.enqueue(GUILD, USER)
    assert not f.rewards.enqueue(GUILD, USER)
    assert len(f.rewards.tasks) == 1
    with pytest.raises(UserError, match='зайнятий'):
        await configure(f)
    await asyncio.gather(*f.rewards.tasks)
    f.add.assert_awaited_once()
    assert not f.rewards.tasks and not f.rewards.active


async def test_unload_cancels_prestarted_tasks_and_denies_new_admission(context):
    f = context
    assert f.rewards.enqueue(GUILD, USER)
    await f.rewards.cog_unload()
    assert not f.rewards.tasks and not f.rewards.active
    assert not f.rewards.enqueue(GUILD, USER)
    f.get_guild.assert_not_awaited()


async def test_unload_joins_active_request(context):
    f = context
    await configure(f)
    await earn(f)
    entered, exited = asyncio.Event(), asyncio.Event()
    async def block(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()
    f.get_guild.side_effect = block
    assert f.rewards.enqueue(GUILD, USER)
    await entered.wait()
    await f.rewards.cog_unload()
    assert exited.is_set() and not f.rewards.tasks and not f.rewards.active
    f.add.assert_not_awaited()


async def test_global_admission_is_bounded(context, monkeypatch):
    f = context
    monkeypatch.setattr(module, 'MAX_ACTIVE', 1)
    assert f.rewards.enqueue(GUILD, USER)
    other = SimpleNamespace(id=11, unavailable=False)
    f.bot._connection._guilds[11] = other
    assert not f.rewards.enqueue(11, USER)
    await f.rewards.cog_unload()


async def test_unknown_http_result_rechecks_roles_on_next_claim(context):
    f = context
    await configure(f)
    await earn(f)
    async def uncertain(guild_id, user_id, role_id, **kwargs):
        f.members[USER]['roles'].append(str(role_id))
        raise discord.HTTPException(SimpleNamespace(status=500, reason='unknown'), 'unknown')
    f.add.side_effect = uncertain
    assert 'не підтвердив' in reply(await claim(f))
    assert 'Нових' in reply(await claim(f))
    f.add.assert_awaited_once()


async def test_cumulative_rewards_keep_lower_roles_and_grant_higher_role(context):
    from test_discord_moderation import role_payload
    f = context
    await configure(f)
    f.roles.append(role_payload(302, 6))
    await f.bot.db.set_level_reward(GUILD, 'text', 3, 302)
    await earn(f, value=400)
    async def add(guild_id, user_id, role_id, **kwargs):
        assert (guild_id, user_id) == (GUILD, USER)
        f.members[user_id]['roles'].append(str(role_id))
    f.add.side_effect = add
    assert '2' in reply(await claim(f))
    assert set(f.members[USER]['roles']) == {str(ROLE), '302'}
    assert 'Нових' in reply(await claim(f))
    assert f.add.await_count == 2
    f.remove.assert_not_awaited()


async def test_privilege_changed_on_one_candidate_prevents_partial_grant(context):
    from test_discord_moderation import role_payload
    f = context
    await configure(f)
    f.roles.append(role_payload(302, 6, discord.Permissions(administrator=True).value))
    await f.bot.db.set_level_reward(GUILD, 'text', 3, 302)
    await earn(f, value=400)
    with pytest.raises(UserError, match='привілейованих'):
        await claim(f)
    f.add.assert_not_awaited()


async def test_disable_during_rest_reads_prevents_first_mutation(context):
    f = context
    await configure(f)
    await earn(f)
    async def rooms(*args):
        await f.bot.db.configure(GUILD, levels=False)
        return [deepcopy(f.room)]
    f.bot.http.get_all_guild_channels.side_effect = rooms
    with pytest.raises(UserError, match='вимкнена'):
        await claim(f)
    f.add.assert_not_awaited()


async def test_missing_member_role_snapshot_fails_closed(context):
    f = context
    await configure(f)
    await earn(f)
    f.members[USER]['roles'] = ['999999']
    with pytest.raises(UserError, match='Ролі учасника'):
        await claim(f)
    f.add.assert_not_awaited()


async def test_bot_loses_manage_roles_after_configuration(context):
    f = context
    await configure(f)
    await earn(f)
    f.roles[2]['permissions'] = '0'
    with pytest.raises(UserError, match='Manage Roles'):
        await claim(f)
    f.add.assert_not_awaited()


async def test_role_moved_above_creator_rejected_even_when_bot_can_grant(context):
    f = context
    f.roles[3]['position'] = 15
    with pytest.raises(UserError, match='нижче вашої'):
        await configure(f)
    assert not await f.bot.db.level_rewards(GUILD)


async def test_disable_during_first_grant_stops_remaining_roles(context):
    from test_discord_moderation import role_payload
    f = context
    await configure(f)
    f.roles.append(role_payload(302, 6))
    await f.bot.db.set_level_reward(GUILD, 'text', 3, 302)
    await earn(f, value=400)
    async def add(guild_id, user_id, role_id, **kwargs):
        f.members[user_id]['roles'].append(str(role_id))
        await f.bot.db.configure(GUILD, levels=False)
    f.add.side_effect = add
    with pytest.raises(UserError, match='подальшу видачу'):
        await claim(f)
    f.add.assert_awaited_once()
    f.remove.assert_not_awaited()
