

import os
import json
import logging
from typing import Any, Dict

import discord
from discord.ext import commands

# Налаштування логування: повідомлення виводитимуться у консоль
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("bot")

class DataManager(commands.Cog):
    """
    Ког для керування даними серверів (збереження/завантаження JSON).
    Працює з папкою 'data/servers'.
    """
    def __init__(self, bot: commands.Bot, base_path: str = "data"):
        self.bot = bot
        # Шлях до папки, де зберігаються файли серверів
        self.base_path = os.path.join(base_path, "servers")
        os.makedirs(self.base_path, exist_ok=True)

    def _get_file_path(self, server_id: int) -> str:
        """
        Отримати шлях до файлу з даними сервера server_id.
        :param server_id: ID сервера (Guild).
        :return: Повний шлях до файлу JSON.
        """
        return os.path.join(self.base_path, f"{server_id}.json")

    def load_server_data(self, server_id: int) -> Dict[str, Any]:
        """
        Завантажити дані сервера з файлу JSON.
        Якщо файл відсутній або пошкоджений – повертає порожній словник.
        :param server_id: ID сервера (Guild).
        :return: Дані у форматі словника.
        """
        file_path = self._get_file_path(server_id)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as file:
                    return json.load(file)
            except json.JSONDecodeError:
                logger.warning(f"Файл пошкоджено: {file_path}. Повертаю порожній об'єкт.")
        return {}

    def save_server_data(self, server_id: int, data: Dict[str, Any]) -> None:
        """
        Зберегти дані сервера у файл JSON.
        :param server_id: ID сервера (Guild).
        :param data: Дані у форматі словника.
        """
        file_path = self._get_file_path(server_id)
        try:
            with open(file_path, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=4)
            logger.info(f"Дані збережено для сервера {server_id} у файл {file_path}.")
        except Exception as e:
            logger.error(f"Не вдалося записати файл {file_path}: {e}")

def setup(bot: commands.Bot):
    """
    Функція підключення Cog до бота (py-cord).
    Викликається при bot.load_extension('data_manager').
    """
    bot.add_cog(DataManager(bot))
    logger.info("DataManager успішно завантажено.")

    