

import discord
from discord.ext import commands
from cache.song_queue_handler import EnhancedCache
import configparser
import logging
import asyncio
import os
import time

# Налаштування логування
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bot")

# Читання конфігурації
config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(__file__), "config", "options.ini")

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

# Додаємо кеш до бота
bot.song_queue = EnhancedCache()

# Прив'язка токена до об'єкта `bot`
bot.token = TOKEN

# Очищення застарілих записів у кеші при запуску
def clean_cache():
    expiration_time = 30 * 24 * 60 * 60  # 30 днів у секундах
    current_time = time.time()
    bot.song_queue.cache = {
        key: value for key, value in bot.song_queue.cache.items()
        if current_time - value.get("timestamp", current_time) < expiration_time
    }
    bot.song_queue.save_cache()

@bot.event
async def on_ready():
    logger.info(f"Бот запущено як {bot.user}")

async def load_extensions():
    """Завантаження всіх розширень."""
    extensions = [
        "extensions.admin",
        "extensions.moderation",
        "extensions.user",
        "commands.music",
        "commands.help",
    ]
    for ext in extensions:
        try:
            await bot.load_extension(ext)
            logger.info(f"Розширення {ext} успішно завантажено.")
        except Exception as e:
            logger.error(f"Помилка при завантаженні розширення {ext}: {e}")

# Головна функція запуску
async def main():
    clean_cache()  # Очищення кешу перед запуском
    async with bot:
        await load_extensions()
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
