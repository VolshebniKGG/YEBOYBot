from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core_v2 import automod as module
from core_v2.automod import AutoMod
from core_v2.security import UserError


@pytest.fixture
def sample():
    guild = SimpleNamespace(id=100, owner_id=999, unavailable=False, me=object())
    author = MagicMock(spec=discord.Member)
    author.id, author.bot = 200, False
    author.guild_permissions = discord.Permissions.none()
    channel = SimpleNamespace(permissions_for=MagicMock(return_value=discord.Permissions(
        view_channel=True, manage_messages=True)))
    message = SimpleNamespace(guild=guild, author=author, webhook_id=None, channel=channel,
                              mentions=[], role_mentions=[], delete=AsyncMock())
    bot = SimpleNamespace(get_guild=lambda ident: guild if ident == guild.id else None,
                          guild_settings=AsyncMock(return_value={"automod": True}),
                          db=SimpleNamespace(configure=AsyncMock()), settings_cache={100: object()})
    clock = [100.0]
    cog = AutoMod(bot, clock=lambda: clock[0])
    return SimpleNamespace(cog=cog, bot=bot, message=message, guild=guild, clock=clock)


async def test_sixth_message_is_flagged_and_deleted_without_reading_content(sample):
    for _ in range(5):
        assert not await sample.cog.inspect_message(sample.message)
    assert await sample.cog.inspect_message(sample.message)
    sample.message.delete.assert_awaited_once()
    assert await sample.cog.inspect_message(sample.message)
    sample.message.delete.assert_awaited_once()  # burst cannot issue another REST call
    sample.clock[0] += 5
    assert not await sample.cog.inspect_message(sample.message)


async def test_mentions_count_unique_users_and_roles(sample):
    sample.message.mentions = [SimpleNamespace(id=1)] * 20
    assert not await sample.cog.inspect_message(sample.message)
    sample.message.mentions = [SimpleNamespace(id=i) for i in range(7)]
    sample.message.role_mentions = [SimpleNamespace(id=55)]
    assert await sample.cog.inspect_message(sample.message)


@pytest.mark.parametrize("flag", ["manage_guild", "manage_messages", "moderate_members", "administrator"])
async def test_moderators_are_exempt(sample, flag):
    setattr(sample.message.author.guild_permissions, flag, True)
    for _ in range(8):
        assert not await sample.cog.inspect_message(sample.message)
    assert not sample.cog.windows
    sample.bot.guild_settings.assert_not_awaited()


@pytest.mark.parametrize("mode", ["disabled", "bot", "webhook", "dm", "owner", "unavailable"])
async def test_exempt_or_disabled_messages_do_not_delete(sample, mode):
    if mode == "disabled":
        sample.bot.guild_settings.return_value = {"automod": False}
    elif mode == "bot":
        sample.message.author.bot = True
    elif mode == "webhook":
        sample.message.webhook_id = 5
    elif mode == "dm":
        sample.message.guild = None
    elif mode == "owner":
        sample.message.author.id = sample.guild.owner_id
    else:
        sample.guild.unavailable = True
    for _ in range(8):
        assert not await sample.cog.inspect_message(sample.message)
    sample.message.delete.assert_not_awaited()


@pytest.mark.parametrize("flag", ["view_channel", "manage_messages"])
async def test_missing_bot_permission_still_suppresses_spam_xp(sample, flag):
    setattr(sample.message.channel.permissions_for.return_value, flag, False)
    sample.message.mentions = [SimpleNamespace(id=i) for i in range(8)]
    assert await sample.cog.inspect_message(sample.message)
    sample.message.delete.assert_not_awaited()


async def test_api_failure_preserves_detection_and_releases_slot(sample):
    sample.message.mentions = [SimpleNamespace(id=i) for i in range(8)]
    sample.message.delete.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
    assert await sample.cog.inspect_message(sample.message)
    assert sample.cog.inflight == 0
    assert await sample.cog.inspect_message(sample.message)
    assert sample.message.delete.await_count == 1


async def test_settings_failure_does_not_punish(sample):
    sample.bot.guild_settings.side_effect = RuntimeError("secret")
    assert not await sample.cog.inspect_message(sample.message)
    sample.message.delete.assert_not_awaited()


async def test_guild_and_user_isolation_and_guild_cooldown(sample):
    sample.message.mentions = [SimpleNamespace(id=i) for i in range(8)]
    assert await sample.cog.inspect_message(sample.message)
    sample.message.author.id = 201
    assert await sample.cog.inspect_message(sample.message)
    assert sample.message.delete.await_count == 1
    sample.guild.id = 101
    assert await sample.cog.inspect_message(sample.message)
    assert sample.message.delete.await_count == 2
    assert set(sample.cog.windows) == {(100, 200), (100, 201), (101, 201)}


async def test_state_and_inflight_are_bounded(sample, monkeypatch):
    monkeypatch.setattr(module, "MAX_USERS", 3)
    monkeypatch.setattr(module, "MAX_GUILDS", 2)
    sample.message.mentions = [SimpleNamespace(id=i) for i in range(8)]
    for ident in range(10):
        sample.guild.id = ident
        sample.message.author.id = ident + 20
        assert await sample.cog.inspect_message(sample.message)
    assert len(sample.cog.windows) == 3
    assert len(sample.cog.guild_deletes) == 2
    sample.cog.inflight = module.MAX_INFLIGHT
    sample.clock[0] += 1
    previous = sample.message.delete.await_count
    assert await sample.cog.inspect_message(sample.message)
    assert sample.message.delete.await_count == previous


async def test_configuration_checks_permission_and_invalidates_cache(sample):
    interaction = SimpleNamespace(guild=sample.guild, user=sample.message.author,
        response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
    with pytest.raises(UserError):
        await AutoMod.automod.callback(sample.cog, interaction, True)
    sample.bot.db.configure.assert_not_awaited()
    interaction.user.guild_permissions.manage_guild = True
    await AutoMod.automod.callback(sample.cog, interaction, True)
    sample.bot.db.configure.assert_awaited_once_with(100, automod=True)
    assert 100 not in sample.bot.settings_cache
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True


async def test_concurrent_events_reserve_budget_before_network_wait(sample):
    import asyncio
    sample.message.mentions = [SimpleNamespace(id=i) for i in range(8)]
    entered, release = asyncio.Event(), asyncio.Event()

    async def delete():
        entered.set()
        await release.wait()

    sample.message.delete.side_effect = delete
    first = asyncio.create_task(sample.cog.inspect_message(sample.message))
    await entered.wait()
    try:
        assert await sample.cog.inspect_message(sample.message)
        assert sample.message.delete.await_count == 1
        assert sample.cog.inflight == 1
    finally:
        release.set()
        assert await first
    assert sample.cog.inflight == 0


async def test_cancelled_deletion_releases_slot(sample):
    import asyncio
    sample.message.mentions = [SimpleNamespace(id=i) for i in range(8)]
    sample.message.delete.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await sample.cog.inspect_message(sample.message)
    assert sample.cog.inflight == 0
