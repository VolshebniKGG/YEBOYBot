

import discord
from discord.ext import commands
from song_queue_handler import EnhancedCache  # Використовуємо кеш
from data_manager import DataManager
import configparser
import logging
import asyncio
import os

# Ініціалізація керування даними
bot.data_manager = DataManager()

@bot.event
async def on_ready():
    logger.info(f"Бот запущено як {bot.user}")

    # Приклад роботи з даними серверів
    server_id = "123456789"
    bot.data_manager.save_data("servers", server_id, {"name": "Test Server", "id": server_id})

    # Приклад роботи з чергою
    bot.data_manager.save_data("queues", server_id, {
        "current_song": {"url": "https://www.youtube.com/watch?v=example", "title": "Example Song"},
        "queue": [{"url": "https://www.youtube.com/watch?v=example2", "title": "Another Song"}]
    })
    queue = bot.data_manager.load_data("queues", server_id)
    logger.info(f"Черга пісень для сервера {server_id}: {queue}")

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
except (configparser.NoSectionError, configparser.NoOptionError) as e:
    logger.error(f"Помилка у файлі конфігурації: {e}")
    exit(1)

# Ініціалізація інтенцій та бота
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Додаємо кеш до бота
bot.song_queue = EnhancedCache()

@bot.event
async def on_ready():
    logger.info(f"Бот запущено як {bot.user}")

    # Додавання тестових даних до черги
    bot.song_queue.add_to_queue("123456789", "https://www.youtube.com/watch?v=example", "Example Song")
    queue = bot.song_queue.get_queue("123456789")
    logger.info(f"Черга пісень для сервера 123456789: {queue}")

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
    async with bot:
        await load_extensions()
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())

