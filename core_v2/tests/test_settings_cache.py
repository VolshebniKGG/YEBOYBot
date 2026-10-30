import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core_v2.bot import Bot
from core_v2.settings_cache import SettingsCache


async def test_old_read_is_retried_after_invalidation_even_when_no_entry_existed():
    started, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def read(guild_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await finish.wait()
            return {"guild_id": guild_id, "automod": True}
        return {"guild_id": guild_id, "automod": False}

    bot = SimpleNamespace(settings_cache=SettingsCache(), db=SimpleNamespace(get_settings=read))
    operation = asyncio.create_task(Bot.guild_settings(bot, 1))
    await started.wait()
    bot.settings_cache.pop(1, None)
    finish.set()
    assert (await operation)["automod"] is False
    assert bot.settings_cache[1][1]["automod"] is False
    assert calls == 2


async def test_cached_read_and_bounded_invalidations():
    bot = SimpleNamespace(settings_cache=SettingsCache(), db=SimpleNamespace(
        get_settings=AsyncMock(return_value={"guild_id": 1, "levels": False})))
    assert await Bot.guild_settings(bot, 1) == await Bot.guild_settings(bot, 1)
    bot.db.get_settings.assert_awaited_once()
    for index in range(20000):
        bot.settings_cache.pop(index, None)
    assert len(bot.settings_cache) == 0
    assert bot.settings_cache.generation == 20000


async def test_persistent_configuration_churn_fails_without_caching_stale_value():
    bot = SimpleNamespace(settings_cache=SettingsCache())

    async def read(guild_id):
        bot.settings_cache.clear()
        return {"guild_id": guild_id, "automod": True}

    bot.db = SimpleNamespace(get_settings=AsyncMock(side_effect=read))
    with pytest.raises(RuntimeError, match="changed repeatedly"):
        await Bot.guild_settings(bot, 1)
    assert not bot.settings_cache
    assert bot.db.get_settings.await_count == 3
