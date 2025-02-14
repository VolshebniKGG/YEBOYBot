

import os
import sys
import logging
import asyncio
import configparser

import discord
from discord.ext import commands

# Встановлюємо рівень логування для discord, щоб не було надмірних повідомлень
logging.getLogger('discord').setLevel(logging.WARNING)

# Імпортуємо модулі налаштування логування та завантаження розширень (cogs)
from .logging_setup import setup_logging
from .moderation import setup as setup_moderation
from .user import setup as setup_user
from .music import setup as setup_music
from .help import setup as setup_help
from .rank import setup as setup_rank
from .autoplaylist import AutoPlaylistManager

# Налаштовуємо логування
setup_logging()
logger = logging.getLogger("bot")

# Читання конфігураційного файлу
config = configparser.ConfigParser()
config_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "config", "options.ini")
)
if not os.path.exists(config_path):
    logger.error(f"Файл конфігурації {config_path} не знайдено.")
    sys.exit(1)
config.read(config_path)

try:
    TOKEN = config.get("Credentials", "Token")
    if not TOKEN:
        raise ValueError("Токен не вказано у файлі конфігурації.")
except (configparser.NoSectionError, configparser.NoOptionError, ValueError) as e:
    logger.error(f"Помилка у файлі конфігурації: {e}")
    sys.exit(1)

def create_bot() -> commands.Bot:
    """Створює новий екземпляр бота із заданими інтенціями та подіями."""
    intents = discord.Intents.all()
    bot = commands.Bot(
        command_prefix=config.get("Chat", "CommandPrefix", fallback="!"),
        intents=intents
    )

    @bot.event
    async def on_ready():
        logger.info(f"Бот запущено як {bot.user} ({bot.user.id})")

    return bot

def load_extensions(bot_instance: commands.Bot) -> None:
    """Завантажуємо всі потрібні розширення (cogs) для бота."""
    setup_moderation(bot_instance)
    setup_user(bot_instance)
    setup_music(bot_instance)
    setup_help(bot_instance)
    setup_rank(bot_instance)
    logger.info("Усі розширення (Cog-и) завантажено успішно.")

async def main():
    global bot  # оголошуємо bot як глобальну, щоб його було видно в інших місцях, якщо потрібно
    bot = create_bot()
    load_extensions(bot)
    try:
        # Використовуємо асинхронний метод старту
        await bot.start(TOKEN)
    except Exception as e:
        logger.error(f"Помилка при старті бота: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())




