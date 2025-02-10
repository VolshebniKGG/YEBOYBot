

import os
import sys
import logging
import asyncio
import configparser

import discord
from discord.ext import commands

# Налаштування логування: встановлюємо рівень для модуля discord на WARNING,
# щоб прибрати INFO-повідомлення від бібліотеки discord.py.
logging.getLogger('discord').setLevel(logging.WARNING)

# Імпортуємо функцію налаштування логування з вашого модуля
from yeboybot.logging_setup import setup_logging

# Імпортуємо функції підключення ког-ів
from yeboybot.moderation import setup as setup_moderation
from yeboybot.user import setup as setup_user
from yeboybot.music import setup as setup_music
from yeboybot.help import setup as setup_help
from yeboybot.rank import setup as setup_rank
from yeboybot.autoplaylist import AutoPlaylistManager

# Налаштування логування: функція setup_logging налаштовує логування з файловими
# та консольними обробниками.
setup_logging()
logger = logging.getLogger("bot")

# Читання конфігурації
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

# Ініціалізація інтенцій та бота
intents = discord.Intents.all()
bot = commands.Bot(
    command_prefix=config.get("Chat", "CommandPrefix", fallback="!"),
    intents=intents
)

@bot.event
async def on_ready():
    logger.info(f"Бот запущено як {bot.user} ({bot.user.id})")

def load_extensions(bot_instance: commands.Bot) -> None:
    """Ручне завантаження всіх ког-ів (extension setup)."""
    setup_moderation(bot_instance)
    setup_user(bot_instance)
    setup_music(bot_instance)
    setup_help(bot_instance)
    setup_rank(bot_instance)
    logger.info("Усі розширення (Cog-и) завантажено успішно.")

def main() -> None:
    """Головна функція запуску бота."""
    load_extensions(bot)
    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"Помилка при запуску бота: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()



