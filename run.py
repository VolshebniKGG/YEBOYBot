



import discord
from discord.ext import commands
import logging
import configparser
import os
import asyncio
from logs.logging_setup import setup_logging
from database.db_manager import DatabaseManager

# Налаштування логування
setup_logging()
logger = logging.getLogger('bot')

# Читання конфігурації
config_path = os.path.join(os.path.dirname(__file__), "config", "options.ini")
if not os.path.exists(config_path):
    raise FileNotFoundError(f"Файл конфігурації не знайдено: {config_path}")

config = configparser.ConfigParser()
config.read(config_path)

TOKEN = config["Bot"]["Token"]
PREFIX = config["Bot"]["PREFIX"]
SPOTIPY_CLIENT_ID = config.get("Spotify", "Client_ID", fallback=None)
SPOTIPY_CLIENT_SECRET = config.get("Spotify", "Client_Secret", fallback=None)

if SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET:
    os.environ["SPOTIPY_CLIENT_ID"] = SPOTIPY_CLIENT_ID
    os.environ["SPOTIPY_CLIENT_SECRET"] = SPOTIPY_CLIENT_SECRET
else:
    logger.warning("Spotify credentials are not set. Music commands may not work correctly.")

# Ініціалізація бази даних
db = DatabaseManager()

# Ініціалізація інтенцій
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Завантаження розширень
async def load_extensions():
    extensions = [
        'extensions.admin',
        'extensions.moderation',
        'extensions.user',
        'commands.help',
        'commands.music'
    ]
    for extension in extensions:
        try:
            logger.info(f"Завантаження розширення {extension}...")
            await bot.load_extension(extension)
            logger.info(f'Розширення {extension} завантажено успішно.')
        except SyntaxError as se:
            logger.error(f'Синтаксична помилка у {extension}: {se}')
        except FileNotFoundError as fnfe:
            logger.error(f'Файл не знайдено для {extension}: {fnfe}')
        except Exception as e:
            logger.error(f'Не вдалося завантажити {extension}: {e}')

# Головна функція
async def main():
    try:
        await load_extensions()
        logger.info("Усі розширення завантажено.")

        print(f"Доступні команди: {[command.name for command in bot.commands]}")
        await bot.start(TOKEN)
    except Exception as e:
        logger.critical(f"Bot failed to start: {e}")

if __name__ == '__main__':
    asyncio.run(main())


