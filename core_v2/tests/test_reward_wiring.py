"""Reward scheduling follows committed eligible XP; no Discord login."""
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core_v2.bot import Bot
from core_v2.config import Settings
from core_v2.help_catalog import collect_commands
from test_voice_xp import harness, minutes


@pytest.mark.parametrize("awarded", [None, 5, 100])
async def test_text_rewards_only_follow_successful_award(awarded):
    rewards = SimpleNamespace(enqueue=Mock())
    bot = SimpleNamespace(
        get_cog=lambda name: rewards if name == "LevelRewards" else None,
        xp_times=OrderedDict(), guild_settings=AsyncMock(return_value={"levels": True}),
        db=SimpleNamespace(award=AsyncMock(return_value=awarded)))
    message = SimpleNamespace(guild=SimpleNamespace(id=101),
        author=SimpleNamespace(id=202, bot=False), webhook_id=None)
    await Bot.on_message(bot, message)
    if awarded is None:
        rewards.enqueue.assert_not_called()
    else:
        rewards.enqueue.assert_called_once_with(101, 202)
    await Bot.on_message(bot, message)
    assert rewards.enqueue.call_count == int(awarded is not None)


@pytest.mark.parametrize("awarded", [None, 5])
async def test_voice_rewards_only_follow_successful_award(awarded):
    cog, clock, guild, room = harness()
    rewards = SimpleNamespace(enqueue=Mock())
    cog.bot.get_cog = lambda name: rewards if name == "LevelRewards" else None
    cog.bot.db.award.return_value = awarded
    await cog.poll()
    rewards.enqueue.assert_not_called()
    await minutes(cog, clock)
    assert rewards.enqueue.call_count == (0 if awarded is None else 2)
    if awarded is not None:
        assert {call.args for call in rewards.enqueue.call_args_list} == {(1, 2), (1, 3)}


async def test_reward_commands_in_help_without_discord_sync(tmp_path):
    from core_v2.level_rewards import LevelRewards
    bot = Bot(Settings("synthetic", f"sqlite+aiosqlite:///{tmp_path / 'rewards.db'}"))
    try:
        await bot.add_cog(LevelRewards(bot))
        entries = {entry.name: entry for entry in collect_commands(bot.tree)}
        for name in ("level-reward set", "level-reward remove", "level-reward list", "claim-rewards"):
            assert entries[name].category == "rank"
        assert bot.tree.get_command("claim-rewards").default_permissions is None
    finally:
        await bot.remove_cog("LevelRewards")
        await bot.db.close()
