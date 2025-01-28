

import json
import os
import logging
from discord.ext.commands import Cog
from typing import Any, Dict

logger = logging.getLogger("bot")

class DataManager(Cog):
    def __init__(self, bot, base_path: str = "data"):
        self.bot = bot
        self.base_path = os.path.join(base_path, "servers")
        os.makedirs(self.base_path, exist_ok=True)

    def _get_file_path(self, server_id: int) -> str:
        """Отримати шлях до файлу для заданого сервера."""
        return os.path.join(self.base_path, f"{server_id}.json")

    def load_server_data(self, server_id: int) -> Dict[str, Any]:
        """Завантажити дані сервера з файлу."""
        file_path = self._get_file_path(server_id)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as file:
                    return json.load(file)
            except json.JSONDecodeError:
                logger.warning(f"Файл пошкоджено: {file_path}. Повертаю порожній об'єкт.")
        return {}

    def save_server_data(self, server_id: int, data: Dict[str, Any]) -> None:
        """Зберегти дані сервера у файл."""
        file_path = self._get_file_path(server_id)
        try:
            with open(file_path, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=4)
            logger.info(f"Дані збережено для сервера {server_id} у файл {file_path}.")
        except Exception as e:
            logger.error(f"Не вдалося записати файл {file_path}: {e}")

async def setup(bot):
    await bot.add_cog(DataManager(bot))
    logger.info("DataManager успішно завантажено.")

# Приклад використання
if __name__ == "__main__":
    data_manager = DataManager()

    # Сервери
    server_id = "123456789"
    server_data = {"name": "Test Server", "id": server_id}
    data_manager.save_data("servers", server_id, server_data)

    # Користувачі
    user_id = "987654321"
    user_data = {"name": "Test User", "rank": 10}
    data_manager.save_data("users", user_id, user_data)

    # Черга
    queue_data = {
        "current_song": {"url": "https://www.youtube.com/watch?v=example", "title": "Example Song"},
        "queue": [
            {"url": "https://www.youtube.com/watch?v=example2", "title": "Another Song"}
        ]
    }
    data_manager.save_data("queues", server_id, queue_data)

    # Завантаження даних
    print("Server Data:", data_manager.load_data("servers", server_id))
    print("User Data:", data_manager.load_data("users", user_id))
    print("Queue Data:", data_manager.load_data("queues", server_id))

    