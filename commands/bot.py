

from discord.ext import commands
import discord
import sys
import os
import configparser

# Додаємо кореневий шлях до системних модулів
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.db_manager import DatabaseManager

# Завантаження токена з файлу конфігурації
config = configparser.ConfigParser()
config.read(os.path.join(os.path.dirname(__file__), "..", "config", "options.ini"))

try:
    TOKEN = config.get("Bot", "Token")
except (configparser.NoSectionError, configparser.NoOptionError):
    print("Помилка: Не вдалося знайти токен у файлі options.ini. Перевірте конфігурацію.")
    sys.exit(1)

# Ініціалізація бази даних
db = DatabaseManager()

# Ініціалізація інтенцій
intents = discord.Intents.all()

# Ініціалізація бота
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Бот запущено як {bot.user}")
    # Перевірка роботи бази даних
    try:
        db.execute_non_query("INSERT INTO users (discord_id, username) VALUES (?, ?)", ("123456789", "TestUser"))
        users = db.execute_query("SELECT * FROM users")
        print("Користувачі в базі даних:", users)
    except Exception as e:
        print(f"Помилка роботи з базою даних: {e}")

# Запуск бота
if __name__ == "__main__":
    bot.run(TOKEN)



