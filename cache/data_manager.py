

import json
import os
import logging

logger = logging.getLogger("bot")

class DataManager:
    def __init__(self, base_path="data"):
        self.base_path = base_path
        self.paths = {
            "servers": os.path.join(self.base_path, "servers"),
            "users": os.path.join(self.base_path, "users"),
            "queues": os.path.join(self.base_path, "queues"),
        }
        self._ensure_directories()

    def _ensure_directories(self):
        """Створити потрібні папки, якщо вони не існують."""
        for path in self.paths.values():
            os.makedirs(path, exist_ok=True)

    def _get_file_path(self, category, identifier):
        """Отримати шлях до файлу для заданої категорії та ідентифікатора."""
        if category not in self.paths:
            raise ValueError(f"Невідома категорія: {category}")
        return os.path.join(self.paths[category], f"{identifier}.json")

    def load_data(self, category, identifier):
        """Завантажити дані з файлу."""
        file_path = self._get_file_path(category, identifier)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as file:
                    return json.load(file)
            except json.JSONDecodeError:
                logger.error(f"Файл пошкоджено: {file_path}. Повертаю порожній об'єкт.")
        return {}

    def save_data(self, category, identifier, data):
        """Зберегти дані до файлу."""
        file_path = self._get_file_path(category, identifier)
        try:
            with open(file_path, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=4)
            logger.info(f"Дані збережено у файл: {file_path}")
        except Exception as e:
            logger.error(f"Не вдалося записати файл {file_path}: {e}")

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

    