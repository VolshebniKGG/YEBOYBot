from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core_v2.bot import Bot
from core_v2.config import Settings


@pytest.mark.parametrize('enabled', [True, False])
async def test_prefix_requires_explicit_message_content_opt_in(tmp_path, enabled):
    bot = Bot(Settings('synthetic', f'sqlite+aiosqlite:///{tmp_path / "bot.db"}', prefix_commands=enabled))
    bot.deliver.start = lambda: None
    try:
        await bot._async_setup_hook()
        await bot.setup_hook()
        assert bot.intents.message_content is enabled
        assert (bot.get_cog('PrefixCommands') is not None) is enabled
        assert bot.command_prefix == '!'
    finally:
        await bot.close()


@pytest.mark.parametrize('blocked,handled', [(True, True), (False, True), (False, False)])
async def test_automod_precedes_prefix_and_commands_do_not_earn_xp(blocked, handled):
    automod = SimpleNamespace(inspect_message=AsyncMock(return_value=blocked))
    prefix = SimpleNamespace(handle_message=AsyncMock(return_value=handled))
    cogs = {'AutoMod': automod, 'PrefixCommands': prefix}
    bot = SimpleNamespace(get_cog=cogs.get, xp_times=OrderedDict(),
        guild_settings=AsyncMock(return_value={'levels': True}), db=SimpleNamespace(award=AsyncMock()))
    message = SimpleNamespace(guild=SimpleNamespace(id=101), author=SimpleNamespace(id=202, bot=False), webhook_id=None)
    await Bot.on_message(bot, message)
    assert prefix.handle_message.await_count == (0 if blocked else 1)
    assert bot.db.award.await_count == (0 if blocked or handled else 1)
