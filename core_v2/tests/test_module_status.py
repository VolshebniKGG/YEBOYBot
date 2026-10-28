"""Current runtime/module evidence; SQL is real where persistence matters."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from sqlalchemy import event

from core_v2 import module_status as module
from core_v2.db import Database
from core_v2.module_status import collect_module_status


def rank_tree():
    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)
    tree = discord.app_commands.CommandTree(client)

    async def rank(interaction):
        pass

    tree.add_command(discord.app_commands.Command(name="rank", description="Ранг", callback=rank))
    tree.sync = AsyncMock(side_effect=AssertionError("must not publish commands"))
    tree.fetch_commands = AsyncMock(side_effect=AssertionError("must not fetch Discord API"))
    return tree


@pytest.fixture
def sample():
    guild = SimpleNamespace(id=101, shard_id=0, unavailable=False, fetch_member=AsyncMock())
    other = SimpleNamespace(id=202, shard_id=1, unavailable=False, fetch_member=AsyncMock())
    guilds = {101: guild, 202: other}
    cogs = {"Core": object(), "VoiceXP": SimpleNamespace(
        collect=SimpleNamespace(is_running=MagicMock(return_value=True)),
        gateway_paused=False, paused_shards=set()), "Music": None}
    intents = discord.Intents.none()
    intents.guild_messages = intents.voice_states = True
    db = SimpleNamespace(
        get_settings=AsyncMock(return_value={"guild_id": 101, "levels": True,
            "automod": True, "welcome_channel": 12345, "autorole": 67890}),
        configure=AsyncMock(side_effect=AssertionError("must not configure")),
        initialize=AsyncMock(side_effect=AssertionError("must not initialize")))
    bot = SimpleNamespace(get_guild=MagicMock(side_effect=guilds.get),
        get_cog=MagicMock(side_effect=cogs.get), is_ready=MagicMock(return_value=True),
        on_message=AsyncMock(), intents=intents, db=db, tree=rank_tree(),
        deliver=SimpleNamespace(is_running=MagicMock(return_value=True)),
        guild_settings=AsyncMock(side_effect=AssertionError("must not use cached settings")),
        settings=SimpleNamespace(music=False, token="synthetic-private-token",
            database="postgresql+asyncpg://user:synthetic-password@host/db", ffmpeg="private-path"))
    return SimpleNamespace(bot=bot, guild=guild, other=other, guilds=guilds, cogs=cogs)


def field(embed, name):
    return next(value.value for value in embed.fields if value.name == name)


def encoded(embed):
    return json.dumps(embed.to_dict(), ensure_ascii=False)


async def test_current_modules_are_private_safe_metadata_without_network_or_writes(sample):
    f = sample
    embed = await collect_module_status(f.bot, f.guild)
    assert "готовність" in embed.description
    assert "Увімкнено" in field(embed, "Текстовий XP")
    assert "Увімкнено" in field(embed, "Голосовий XP")
    assert "5 XP" in field(embed, "Голосовий XP")
    assert "підключена" in field(embed, "Картка рангу")
    assert "працює" in field(embed, "Нагадування")
    assert "Вимкнено" in field(embed, "Музика")
    f.bot.db.get_settings.assert_awaited_once_with(101)
    f.bot.db.configure.assert_not_awaited()
    f.bot.db.initialize.assert_not_awaited()
    f.bot.guild_settings.assert_not_awaited()
    f.bot.tree.sync.assert_not_awaited()
    f.bot.tree.fetch_commands.assert_not_awaited()
    f.guild.fetch_member.assert_not_awaited()
    assert len(embed.fields) <= 25
    total = len(embed.title) + len(embed.description) + len(embed.footer.text)
    total += sum(len(item.name) + len(item.value) for item in embed.fields)
    assert total < 6000
    for item in embed.fields:
        assert len(item.name.encode("utf-16-le")) // 2 <= 256
        assert len(item.value.encode("utf-16-le")) // 2 <= 1024
    rendered = encoded(embed)
    for forbidden in ("synthetic-private-token", "synthetic-password", "private-path", "12345", "67890"):
        assert forbidden not in rendered


@pytest.mark.parametrize("message_content", [False, True])
async def test_unimplemented_modules_never_activate_from_settings_or_intents(sample, message_content):
    sample.bot.intents.message_content = message_content
    sample.bot.db.get_settings.return_value.update(automod=True, welcome_channel=777, autorole=888)
    result = await collect_module_status(sample.bot, sample.guild)
    text = field(result, "Ще не підключено")
    for label in ("Автомодерація", "привітання", "авторолі", "панелі ролей", "нагороди XP", "журнали подій"):
        assert label in text
    assert "не підключені" in text
    assert "777" not in encoded(result) and "888" not in encoded(result)


async def test_disabled_levels_disable_both_paths_without_deleting_data(sample):
    sample.bot.db.get_settings.return_value["levels"] = False
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Вимкнено налаштуваннями" in field(result, "Текстовий XP")
    assert "Вимкнено налаштуваннями" in field(result, "Голосовий XP")
    sample.bot.db.configure.assert_not_awaited()


@pytest.mark.parametrize("value", [None, 0, 1, "false", "true", {}, []],
                         ids=["missing", "zero", "one", "false-string", "true-string", "object", "list"])
async def test_non_boolean_levels_are_unknown_instead_of_truthy(sample, value):
    sample.bot.db.get_settings.return_value["levels"] = value
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Невідомо" in field(result, "Текстовий XP")
    assert "Невідомо" in field(result, "Голосовий XP")


@pytest.mark.parametrize("intent,label", [("guild_messages", "Текстовий XP"), ("voice_states", "Голосовий XP")])
async def test_missing_required_intent_is_not_enabled(sample, intent, label):
    setattr(sample.bot.intents, intent, False)
    result = await collect_module_status(sample.bot, sample.guild)
    assert "немає потрібного intent" in field(result, label)
    assert "Увімкнено" not in field(result, label)


async def test_text_handler_and_voice_cog_are_independent_of_core_cog(sample):
    sample.cogs["Core"] = None
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Увімкнено" in field(result, "Текстовий XP")
    assert "Увімкнено" in field(result, "Голосовий XP")
    assert "Не підключено" in field(result, "Картка рангу")
    assert "Не підключено" in field(result, "Модерація")
    sample.bot.on_message = None
    sample.cogs["VoiceXP"] = None
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Не підключено" in field(result, "Текстовий XP")
    assert "Не підключено" in field(result, "Голосовий XP")


async def test_rank_requires_local_registered_command_not_only_cog(sample):
    sample.bot.tree.remove_command("rank")
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Не підключено" in field(result, "Картка рангу")


async def test_rank_can_be_registered_only_for_this_guild(sample):
    command = sample.bot.tree.remove_command("rank")
    sample.bot.tree.add_command(command, guild=discord.Object(id=101))
    first = await collect_module_status(sample.bot, sample.guild)
    second = await collect_module_status(sample.bot, sample.other)
    assert "підключена" in field(first, "Картка рангу")
    assert "Не підключено" in field(second, "Картка рангу")


@pytest.mark.parametrize("ready", [False, None, "yes"], ids=["not-ready", "unknown", "wrong-type"])
async def test_not_ready_client_never_claims_active_xp(sample, ready):
    sample.bot.is_ready.return_value = ready
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Увімкнено" not in field(result, "Текстовий XP")
    assert "Увімкнено" not in field(result, "Голосовий XP")
    expected = ("Discord-клієнт ще не підтвердив готовність." if ready is False else
                "Готовність Discord-клієнта невідома.")
    assert result.description == expected


async def test_unavailable_or_removed_guild_skips_database_reads(sample):
    sample.guild.unavailable = True
    result = await collect_module_status(sample.bot, sample.guild)
    assert "недоступний" in field(result, "Текстовий XP")
    sample.bot.db.get_settings.assert_not_awaited()
    sample.guild.unavailable = False
    sample.guilds.pop(101)
    result = await collect_module_status(sample.bot, sample.guild)
    assert "недоступний" in field(result, "Голосовий XP")
    sample.bot.db.get_settings.assert_not_awaited()


@pytest.mark.parametrize("row", [None, [], {"guild_id": 202, "levels": True}, {"guild_id": True, "levels": True}],
                         ids=["missing-row", "wrong-row-type", "cross-guild", "boolean-id"])
async def test_invalid_settings_row_is_unknown(sample, row):
    sample.bot.db.get_settings.return_value = row
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Невідомо" in field(result, "Текстовий XP")
    assert "Невідомо" in field(result, "Налаштування сервера")


async def test_database_error_never_echoes_exception_or_claims_xp_is_zero(sample, caplog):
    sample.bot.db.get_settings.side_effect = RuntimeError("synthetic-secret private database URI")
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Невідомо" in field(result, "Текстовий XP")
    assert "Невідомо" in field(result, "Нагадування")
    assert "synthetic-secret" not in encoded(result) + caplog.text
    assert "0 XP" not in encoded(result)


async def test_database_timeout_cancels_read_and_reports_unknown(sample, monkeypatch):
    cancelled = asyncio.Event()

    async def blocked(_guild_id):
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(module, "SETTINGS_TIMEOUT", .01)
    sample.bot.db.get_settings.side_effect = blocked
    result = await collect_module_status(sample.bot, sample.guild)
    assert cancelled.is_set()
    assert "Невідомо" in field(result, "Голосовий XP")


async def test_collection_cancellation_is_not_converted_to_success(sample):
    sample.bot.db.get_settings.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await collect_module_status(sample.bot, sample.guild)


async def test_removed_guild_after_pending_sql_does_not_publish_stale_enabled_status(sample):
    entered, release = asyncio.Event(), asyncio.Event()

    async def read(_guild_id):
        entered.set()
        await release.wait()
        return {"guild_id": 101, "levels": True}

    sample.bot.db.get_settings.side_effect = read
    task = asyncio.create_task(collect_module_status(sample.bot, sample.guild))
    try:
        await entered.wait()
        sample.guilds.pop(101)
        release.set()
        result = await task
        assert "Увімкнено" not in field(result, "Текстовий XP")
        assert "недоступний" in field(result, "Голосовий XP")
        assert "Невідомо" in field(result, "Налаштування сервера")
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("running", [False, None, "true"], ids=["stopped", "unknown", "wrong-type"])
async def test_scheduler_status_uses_actual_running_boolean(sample, running):
    sample.bot.deliver.is_running.return_value = running
    result = await collect_module_status(sample.bot, sample.guild)
    assert "не працює" in field(result, "Нагадування") if running is False else "Невідомо" in field(result, "Нагадування")


async def test_missing_music_cog_distinguishes_disabled_from_disconnected_configuration(sample):
    sample.bot.settings.music = True
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Не підключено" in field(result, "Музика")
    sample.bot.settings.music = None
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Невідомо" in field(result, "Музика")


def music_cog():
    return SimpleNamespace(_closing=False, _teardowns={}, sessions={})


async def test_loaded_music_is_available_even_if_startup_flag_disagrees(sample):
    sample.cogs["Music"] = music_cog()
    sample.bot.settings.music = False
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Доступно, але плеєр не підключений" in field(result, "Музика")


@pytest.mark.parametrize("connected,expected", [(True, "з'єднання підтверджено"),
    (False, "не підтверджено"), (None, "невідомий"), ("true", "невідомий")],
                         ids=["connected", "disconnected", "unknown", "wrong-type"])
async def test_music_voice_state_needs_actual_backend_confirmation(sample, connected, expected):
    music = music_cog()
    music.sessions[101] = SimpleNamespace(backend=SimpleNamespace(is_connected=MagicMock(return_value=connected)))
    sample.cogs["Music"] = music
    result = await collect_module_status(sample.bot, sample.guild)
    assert expected in field(result, "Музика")
    if connected is True:
        assert "звучання цей звіт не перевіряє" in field(result, "Музика")
    assert "грає" not in field(result, "Музика")


@pytest.mark.parametrize("closing", [True, False], ids=["unload", "teardown"])
async def test_music_closing_or_pending_teardown_never_claims_audio_is_playing(sample, closing):
    music = music_cog()
    music._closing = closing
    music._teardowns = {} if closing else {101: object()}
    sample.cogs["Music"] = music
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Завершує роботу" in field(result, "Музика")


async def test_music_session_from_another_guild_does_not_appear_connected(sample):
    music = music_cog()
    music.sessions[202] = SimpleNamespace(backend=SimpleNamespace(is_connected=MagicMock(return_value=True)))
    sample.cogs["Music"] = music
    result = await collect_module_status(sample.bot, sample.guild)
    assert "не підключений" in field(result, "Музика")
    music.sessions[202].backend.is_connected.assert_not_called()


async def test_music_without_voice_states_intent_is_unavailable_even_when_cog_loaded(sample):
    sample.cogs["Music"] = music_cog()
    sample.bot.intents.voice_states = False
    result = await collect_module_status(sample.bot, sample.guild)
    assert "немає потрібного intent Voice States" in field(result, "Музика")


@pytest.mark.parametrize("mode", ["collector-stopped", "gateway-paused", "guild-shard-paused"])
async def test_voice_runtime_pause_is_not_reported_as_enabled(sample, mode):
    voice = sample.cogs["VoiceXP"]
    if mode == "collector-stopped":
        voice.collect.is_running.return_value = False
    elif mode == "gateway-paused":
        voice.gateway_paused = True
    else:
        voice.paused_shards.add(0)
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Призупинено" in field(result, "Голосовий XP")
    assert "Увімкнено" not in field(result, "Голосовий XP")
    assert "Увімкнено" in field(result, "Текстовий XP")


async def test_other_shard_pause_does_not_disable_this_guild_voice_collection(sample):
    sample.cogs["VoiceXP"].paused_shards.add(1)
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Увімкнено" in field(result, "Голосовий XP")


@pytest.mark.parametrize("attribute", ["collect", "gateway_paused", "paused_shards"])
async def test_unknown_voice_runtime_probe_stays_unknown(sample, attribute):
    delattr(sample.cogs["VoiceXP"], attribute)
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Невідомо" in field(result, "Голосовий XP")


async def test_ready_is_sampled_after_pending_settings_read(sample):
    entered, release = asyncio.Event(), asyncio.Event()

    async def read(_guild_id):
        entered.set()
        await release.wait()
        return {"guild_id": 101, "levels": True}

    sample.bot.db.get_settings.side_effect = read
    task = asyncio.create_task(collect_module_status(sample.bot, sample.guild))
    try:
        await entered.wait()
        sample.bot.is_ready.return_value = False
        release.set()
        result = await task
        assert result.description == "Discord-клієнт ще не підтвердив готовність."
        assert "Очікує готовності" in field(result, "Голосовий XP")
        assert "Увімкнено" not in field(result, "Текстовий XP")
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("attribute", ["_closing", "_teardowns"])
async def test_unknown_music_lifecycle_never_becomes_available(sample, attribute):
    music = music_cog()
    delattr(music, attribute)
    sample.cogs["Music"] = music
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Невідомо" in field(result, "Музика")
    assert "Доступно" not in field(result, "Музика")


async def test_probe_failures_are_unknown_without_raw_object_repr(sample, caplog):
    def fail(*args):
        raise RuntimeError("synthetic-private-payload")

    sample.bot.get_cog.side_effect = fail
    sample.bot.is_ready.side_effect = fail
    sample.bot.deliver.is_running.side_effect = fail
    sample.bot.tree.get_command = fail
    result = await collect_module_status(sample.bot, sample.guild)
    assert "Невідомо" in field(result, "Музика")
    assert "Невідомо" in field(result, "Картка рангу")
    assert "synthetic-private-payload" not in encoded(result) + caplog.text


async def test_real_sqlite_settings_are_fresh_guild_scoped_and_read_only(sample, tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'modules.db'}")
    await db.initialize()
    await db.configure(101, levels=False, automod=True, welcome_channel=987654321)
    await db.configure(202, levels=True)
    sample.bot.db = db
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(db.engine.sync_engine, "before_cursor_execute", capture)
    try:
        first = await collect_module_status(sample.bot, sample.guild)
        second = await collect_module_status(sample.bot, sample.other)
        assert "Вимкнено налаштуваннями" in field(first, "Текстовий XP")
        assert "Увімкнено" in field(second, "Текстовий XP")
        assert len(statements) == 2 and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
        assert "987654321" not in encoded(first)
        sample.bot.guild_settings.assert_not_awaited()
        await db.configure(101, levels=True)
        third = await collect_module_status(sample.bot, sample.guild)
        assert "Увімкнено" in field(third, "Голосовий XP")
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", capture)
        await db.close()
