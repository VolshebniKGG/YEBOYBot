

import os
import logging
import asyncio
import configparser

import discord
from discord.ext import commands

from yeboybot.logging_setup import setup_logging

from yeboybot.admin import setup as setup_admin
from yeboybot.moderation import setup as setup_moderation
from yeboybot.user import setup as setup_user
from yeboybot.music import setup as setup_music
from yeboybot.help import setup as setup_help
from yeboybot.rank import setup as setup_rank

# Налаштування логування
setup_logging()
logger = logging.getLogger("bot")

# Читання конфігурації
config = configparser.ConfigParser()
config_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "config", "options.ini")
)

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
bot = commands.Bot(
    command_prefix=config.get("Bot", "PREFIX", fallback="!"),
    intents=intents
)

@bot.event
async def on_ready():
    logger.info(f"Бот запущено як {bot.user} ({bot.user.id})")

def load_extensions(bot_instance: commands.Bot):
    """Ручне завантаження всіх когів (extension setup)."""
    setup_admin(bot_instance)
    setup_moderation(bot_instance)
    setup_user(bot_instance)
    setup_music(bot_instance)
    setup_help(bot_instance)
    setup_rank(bot_instance)
    logger.info("Усі розширення (Cog-и) завантажено успішно.")

def main():
    """Головна функція запуску бота."""
    load_extensions(bot)
    # Запускаємо бота (блокуючий виклик)
    bot.run(TOKEN)

if __name__ == "__main__":
    main()


