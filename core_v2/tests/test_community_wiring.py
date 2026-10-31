"""Integration of community modules with the existing command/XP pipeline."""
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core_v2.bot import Bot
from core_v2.config import Settings
from core_v2.help_catalog import collect_commands


async def test_community_commands_registered_and_discoverable(tmp_path):
    from core_v2.community import Community
    from core_v2.automod import AutoMod
    bot = Bot(Settings('synthetic', f'sqlite+aiosqlite:///{tmp_path / "wiring.db"}', members=True))
    try:
        await bot.add_cog(Community(bot))
        await bot.add_cog(AutoMod(bot))
        entries = {entry.name: entry for entry in collect_commands(bot.tree)}
        for name in ('community welcome', 'community autorole', 'community logs', 'automod'):
            assert entries[name].category == 'community'
        assert bot._connection.member_cache_flags.joined
    finally:
        await bot.remove_cog('Community')
        await bot.remove_cog('AutoMod')
        await bot.db.close()


@pytest.mark.parametrize('flagged', [True, False])
async def test_automod_runs_before_xp_and_excludes_flagged_messages(flagged):
    automod = SimpleNamespace(inspect_message=AsyncMock(return_value=flagged))
    bot = SimpleNamespace(get_cog=lambda name: automod if name == "AutoMod" else None, xp_times=OrderedDict(),
        guild_settings=AsyncMock(return_value={'levels': True}),
        db=SimpleNamespace(award=AsyncMock()))
    message = SimpleNamespace(guild=SimpleNamespace(id=101),
        author=SimpleNamespace(id=202, bot=False), webhook_id=None)
    await Bot.on_message(bot, message)
    automod.inspect_message.assert_awaited_once_with(message)
    if flagged:
        bot.db.award.assert_not_awaited()
        assert not bot.xp_times
    else:
        bot.db.award.assert_awaited_once_with(101, 202)


async def test_automod_failure_does_not_award_xp():
    automod = SimpleNamespace(inspect_message=AsyncMock(side_effect=RuntimeError('synthetic')))
    bot = SimpleNamespace(get_cog=lambda _: automod, xp_times=OrderedDict(),
        db=SimpleNamespace(award=AsyncMock()))
    message = SimpleNamespace(guild=SimpleNamespace(id=101),
        author=SimpleNamespace(id=202, bot=False), webhook_id=None)
    await Bot.on_message(bot, message)
    bot.db.award.assert_not_awaited()


@pytest.mark.parametrize("start", [0.0, 0.5, 10.0, 59.0])
async def test_first_message_xp_does_not_depend_on_machine_uptime(monkeypatch, start):
    import core_v2.bot as module
    clock = [start]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    bot = SimpleNamespace(get_cog=lambda _: None, xp_times=OrderedDict(),
        guild_settings=AsyncMock(return_value={"levels": True}),
        db=SimpleNamespace(award=AsyncMock()))
    message = SimpleNamespace(guild=SimpleNamespace(id=101),
        author=SimpleNamespace(id=202, bot=False), webhook_id=None)
    await Bot.on_message(bot, message)
    bot.db.award.assert_awaited_once_with(101, 202)
    clock[0] = start + 59
    await Bot.on_message(bot, message)
    assert bot.db.award.await_count == 1
    clock[0] = start + 60
    await Bot.on_message(bot, message)
    assert bot.db.award.await_count == 2
