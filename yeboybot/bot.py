

import discord
from discord.ext import commands
import configparser
import logging
import asyncio
import os
from yeboybot.logging_setup import setup_logging
from yeboybot.admin import setup as setup_admin
from yeboybot.moderation import setup as setup_moderation
from yeboybot.user import setup as setup_user
from yeboybot.music import setup as setup_music
from yeboybot.help import setup as setup_help

# Налаштування логування
setup_logging()
logger = logging.getLogger("bot")

# Читання конфігурації
config = configparser.ConfigParser()
config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config", "options.ini"))

if not os.path.exists(config_path):
    logger.error(f"Файл конфігурації {config_path} не знайдено.")
    exit(1)

config.read(config_path)

try:
    TOKEN = config.get("Bot", "Token")
    if not TOKEN:
        raise ValueError("Токен не вказано у файлі конфігурації.")
except (configparser.NoSectionError, configparser.NoOptionError, ValueError) as e:
    logger.error(f"Помилка у файлі конфігурації: {e}")
    exit(1)

# Ініціалізація інтенцій та бота
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=config.get("Bot", "PREFIX", fallback="!"), intents=intents)

@bot.event
async def on_ready():
    logger.info(f"Бот запущено як {bot.user}")

async def load_extensions(bot):
    """Ручне завантаження всіх розширень."""
    await setup_admin(bot)
    await setup_moderation(bot)
    await setup_user(bot)
    await setup_music(bot)
    await setup_help(bot)

# Головна функція запуску
async def main():
    async with bot:
        await load_extensions(bot)
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())

