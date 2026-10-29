"""Read-only, guild-scoped module overview from current runtime evidence.

This report describes connected code and observed settings. It does not make
Discord API requests, test audio playback, publish commands or change settings.
"""
import asyncio
from collections.abc import Mapping

import discord


SETTINGS_TIMEOUT = 3
_UNKNOWN = object()


def _read(operation):
    try:
        return operation()
    except Exception:
        # SDK/parser errors can contain paths, URLs or credentials. Only report
        # a fixed unknown state; never stringify the object or the exception.
        return _UNKNOWN


def _boolean(value):
    return value if type(value) is bool else _UNKNOWN


def _available(bot, guild):
    guild_id = getattr(guild, "id", None)
    if type(guild_id) is not int or not 0 < guild_id < 2**63:
        return False
    cached = _read(lambda: bot.get_guild(guild_id))
    if cached is _UNKNOWN:
        return _UNKNOWN
    if cached is not guild:
        return False
    unavailable = _boolean(getattr(guild, "unavailable", _UNKNOWN))
    return not unavailable if unavailable is not _UNKNOWN else _UNKNOWN


def _command(bot, guild_id, name):
    local = _read(lambda: bot.tree.get_command(name, guild=discord.Object(id=guild_id)))
    if local is _UNKNOWN:
        return _UNKNOWN
    if local is not None:
        return True
    command = _read(lambda: bot.tree.get_command(name))
    return command is not None if command is not _UNKNOWN else _UNKNOWN


def _feature_ready(available, ready):
    if available is False:
        return "Сервер тимчасово недоступний."
    if available is _UNKNOWN or ready is _UNKNOWN:
        return "Невідомо: готовність не підтверджена."
    if ready is False:
        return "Очікує готовності Discord-клієнта."
    return None


def _xp(*, available, ready, levels, connected, intent, voice):
    if levels is False:
        return "Вимкнено налаштуваннями сервера. Увімкнення: `/levels enabled:true` із дозволом «Керувати сервером»."
    if connected is False:
        return "Не підключено в поточному процесі."
    if intent is False:
        intent_name = "Voice States" if voice else "Guild Messages"
        return f"Недоступно: немає потрібного intent {intent_name}."
    blocked = _feature_ready(available, ready)
    if blocked:
        return blocked
    if levels is _UNKNOWN or connected is _UNKNOWN or intent is _UNKNOWN:
        return "Невідомо: налаштування або потрібний обробник не підтверджені."
    if voice:
        return "Увімкнено: 5 XP за повну спостережену хвилину з ≥2 придатними учасниками. Аудіо не аналізується."
    return "Увімкнено: 5 XP за подію повідомлення, не частіше разу на 60 секунд. Зміст повідомлення не потрібний."


def _music(bot, guild_id, cog, available, ready):
    if cog is _UNKNOWN:
        return "Невідомо: стан музичного модуля недоступний."
    if cog is None:
        enabled = _boolean(_read(lambda: bot.settings.music))
        if enabled is False:
            return "Вимкнено. Власник бота може ввімкнути музичний модуль; потім використайте `/help` → Музика."
        if enabled is True:
            return "Не підключено в поточному процесі, хоча запуск модуля налаштований. Зверніться до власника бота."
        return "Невідомо: модуль не підключений, конфігурацію не підтверджено."
    closing = _boolean(_read(lambda: cog._closing))
    teardown = _read(lambda: guild_id in cog._teardowns)
    if closing is True or teardown is True:
        return "Завершує роботу. Повторне підключення можливе після завершення від'єднання."
    if closing is _UNKNOWN or teardown is _UNKNOWN:
        return "Невідомо: життєвий цикл музичного плеєра не підтверджено."
    voice_intent = _boolean(_read(lambda: bot.intents.voice_states))
    if voice_intent is False:
        return "Недоступно: немає потрібного intent Voice States."
    if voice_intent is _UNKNOWN:
        return "Невідомо: потрібний intent Voice States не підтверджено."
    blocked = _feature_ready(available, ready)
    if blocked:
        return blocked
    session = _read(lambda: cog.sessions.get(guild_id))
    if session is _UNKNOWN:
        return "Доступно; стан голосового підключення невідомий."
    if session is None:
        return "Доступно, але плеєр не підключений до голосового каналу цього сервера. Почніть із `/help` → Музика."
    failed = _boolean(_read(lambda: getattr(session, "close_failed", False)))
    stopping = _boolean(_read(lambda: getattr(session, "closing", False)))
    if failed is True:
        return ("Від'єднання або очищення аудіо не підтверджено. Нове підключення заблоковано. "
                "Модератор із дозволом «Керувати сервером» може повторити `/music stop`; "
                "якщо збій повторюється, зверніться до власника бота.")
    if stopping is True:
        return "Завершує роботу. Нове підключення заблоковано до підтвердженого від'єднання й очищення аудіо."
    if failed is _UNKNOWN or stopping is _UNKNOWN:
        return "Невідомо: завершення музичної сесії не підтверджено."
    connected = _boolean(_read(lambda: session.backend.is_connected()))
    if connected is True:
        return "Голосове з'єднання підтверджено. Черга та стан: `/music queue`. Саме звучання цей звіт не перевіряє."
    if connected is False:
        return "Голосове з'єднання зараз не підтверджено; плеєр може очікувати відновлення. Перевірте `/music queue`."
    return "Доступно; стан голосового підключення невідомий."


def _voice_status(bot, guild, cog, available, ready, levels, intent):
    connected = cog is not None if cog is not _UNKNOWN else _UNKNOWN
    basic = _xp(available=available, ready=ready, levels=levels,
                connected=connected, intent=intent, voice=True)
    if (levels is not True or connected is not True or intent is not True
            or available is not True or ready is not True):
        return basic
    running = _boolean(_read(lambda: cog.collect.is_running()))
    gateway_paused = _boolean(_read(lambda: cog.gateway_paused))
    shard_paused = _boolean(_read(lambda: guild.shard_id in cog.paused_shards))
    if gateway_paused is True or shard_paused is True:
        return "Призупинено: потрібно відновити спостереження за Discord-з'єднанням цього сервера."
    if running is False:
        return "Призупинено: збирач голосової активності зараз не працює."
    if running is _UNKNOWN or gateway_paused is _UNKNOWN or shard_paused is _UNKNOWN:
        return "Невідомо: роботу збирача голосової активності не підтверджено."
    return basic


async def collect_module_status(bot, guild):
    """Return a bounded Ukrainian embed without writes or external API calls.

    The caller must validate the guild interaction and send the result privately
    with AllowedMentions.none(). The returned report contains no guild/user IDs,
    URLs, channel configuration, token, database connection string or raw errors.
    """
    available = _available(bot, guild)
    guild_id = getattr(guild, "id", None)
    levels = _UNKNOWN
    settings_read = False
    if available is True:
        try:
            async with asyncio.timeout(SETTINGS_TIMEOUT):
                row = await bot.db.get_settings(guild_id)
            if (isinstance(row, Mapping) and type(row.get("guild_id")) is int
                    and row["guild_id"] == guild_id):
                settings_read = True
                levels = _boolean(row.get("levels", _UNKNOWN))
        except Exception:
            pass
    # A guild can leave the client while SQL is pending. Do not present stale
    # settings as an active module on a now-unavailable server.
    available = _available(bot, guild)
    ready = _boolean(_read(lambda: bot.is_ready()))
    if available is not True:
        levels = _UNKNOWN
        settings_read = False

    core = _read(lambda: bot.get_cog("Core"))
    voice = _read(lambda: bot.get_cog("VoiceXP"))
    music = _read(lambda: bot.get_cog("Music"))
    text_handler = _read(lambda: callable(bot.on_message))
    text_intent = _boolean(_read(lambda: bot.intents.guild_messages))
    voice_intent = _boolean(_read(lambda: bot.intents.voice_states))

    description = ("Discord-клієнт підтвердив готовність." if ready is True else
                   "Discord-клієнт ще не підтвердив готовність." if ready is False else
                   "Готовність Discord-клієнта невідома.")
    embed = discord.Embed(title="Модулі YEBOYBot", description=description, colour=0x6E63BD)
    embed.add_field(name="Текстовий XP", value=_xp(available=available, ready=ready,
        levels=levels, connected=text_handler, intent=text_intent, voice=False), inline=False)
    embed.add_field(name="Голосовий XP", value=_voice_status(bot, guild, voice,
        available, ready, levels, voice_intent), inline=False)

    rank = _command(bot, guild_id, "rank") if type(guild_id) is int and guild_id > 0 else _UNKNOWN
    if core is None or rank is False:
        rank_status = "Не підключено в поточному процесі."
    elif core is _UNKNOWN or rank is _UNKNOWN:
        rank_status = "Невідомо: команду не підтверджено."
    else:
        rank_status = "Команда `/rank` підключена. Для PNG у каналі потрібні Attach Files та Embed Links."
    embed.add_field(name="Картка рангу", value=rank_status, inline=False)
    moderation = ("Команди підключені. Потрібні дозволи й ієрархія залежать від дії; див. `/help` → Модерація."
                  if core is not None and core is not _UNKNOWN else
                  "Не підключено в поточному процесі." if core is None else
                  "Невідомо: модуль не підтверджено.")
    embed.add_field(name="Модерація", value=moderation, inline=False)

    running = _boolean(_read(lambda: bot.deliver.is_running()))
    blocked = _feature_ready(available, ready)
    if running is False:
        reminders = "Планувальник не працює в поточному процесі. Доставка нагадувань не підтверджена."
    elif blocked:
        reminders = blocked
    elif running is _UNKNOWN or not settings_read:
        reminders = "Невідомо: стан планувальника або БД не підтверджено."
    else:
        reminders = "Планувальник працює. Доставка залежить від доступу до каналу; довідка: `/help` → Нагадування."
    embed.add_field(name="Нагадування", value=reminders, inline=False)
    embed.add_field(name="Музика", value=_music(bot, guild_id, music, available, ready), inline=False)
    embed.add_field(name="Ще не підключено", value=(
        "Автомодерація, привітання, авторолі, панелі ролей, нагороди XP та журнали подій ще не підключені. "
        "Увімкнення intent або поля налаштувань саме собою їх не активує."), inline=False)
    embed.add_field(name="Налаштування сервера", value=(
        "Прочитано поточні налаштування саме цього сервера." if settings_read else
        "Невідомо: не вдалося підтвердити поточні налаштування сервера. Спробуйте пізніше."), inline=False)
    embed.set_footer(text="Звіт читає локальний стан процесу. Він не виконує дій, не змінює налаштувань і не підтверджує всі сценарії Discord API.")
    return embed
