


import json
import os
import time
import logging

logger = logging.getLogger("bot")

class EnhancedCache:
    def __init__(self, file_path="cache/song_queue.json", backup_path="cache/backup/"):
        self.file_path = file_path
        self.backup_path = backup_path
        self.cache = {}
        self.last_save_time = 0
        self.save_interval = 60  # Інтервал автоматичного збереження (секунди)
        self.backup_retention_time = 7 * 24 * 3600  # Ліміт часу резервних копій: 7 днів
        self._load_cache()

    def _load_cache(self):
        """Завантажити дані з JSON у кеш. Перевірити цілісність."""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, "r", encoding="utf-8") as file:
                    self.cache = json.load(file)
                    logger.info(f"Дані завантажено з {self.file_path}")
            else:
                self.cache = {"servers": {}}
                self._save_cache()
        except json.JSONDecodeError:
            logger.error(f"Файл {self.file_path} пошкоджено. Використовується порожній кеш.")
            self.cache = {"servers": {}}
            self._save_cache()

    def _save_cache(self, force=False):
        """Зберегти кеш у файл. Робить резервну копію."""
        if not force and time.time() - self.last_save_time < self.save_interval:
            return

        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        os.makedirs(self.backup_path, exist_ok=True)

        # Видалення старих резервних копій
        self._clean_old_backups()

        # Резервна копія
        backup_file = os.path.join(self.backup_path, f"song_queue_backup_{int(time.time())}.json")
        if os.path.exists(self.file_path):
            os.replace(self.file_path, backup_file)
            logger.info(f"Резервна копія створена: {backup_file}")

        # Запис нових даних
        try:
            with open(self.file_path, "w", encoding="utf-8") as file:
                json.dump(self.cache, file, indent=4)
                self.last_save_time = time.time()
                logger.info(f"Дані збережено у {self.file_path}")
        except Exception as e:
            logger.error(f"Помилка запису файлу {self.file_path}: {e}")

    def _clean_old_backups(self):
        """Видалити старі резервні копії, що перевищують ліміт часу."""
        current_time = time.time()
        if not os.path.exists(self.backup_path):
            return

        for backup_file in os.listdir(self.backup_path):
            backup_file_path = os.path.join(self.backup_path, backup_file)
            if os.path.isfile(backup_file_path):
                file_age = current_time - os.path.getmtime(backup_file_path)
                if file_age > self.backup_retention_time:
                    os.remove(backup_file_path)
                    logger.info(f"Стара резервна копія видалена: {backup_file_path}")

    def add_to_queue(self, server_id, url, title=None):
        """Додає пісню до черги."""
        if server_id not in self.cache["servers"]:
            self.cache["servers"][server_id] = {"current_song": None, "queue": []}
        self.cache["servers"][server_id]["queue"].append({"url": url, "title": title or url})
        self._save_cache()

    def get_queue(self, server_id):
        """Отримує чергу для конкретного сервера."""
        return self.cache["servers"].get(server_id, {}).get("queue", [])

    def get_next_song(self, server_id):
        """Отримує наступну пісню та видаляє її з черги."""
        if server_id in self.cache["servers"] and self.cache["servers"][server_id]["queue"]:
            next_song = self.cache["servers"][server_id]["queue"].pop(0)
            self.cache["servers"][server_id]["current_song"] = next_song
            self._save_cache()
            return next_song
        return None

    def clear_queue(self, server_id):
        """Очищає чергу для конкретного сервера."""
        if server_id in self.cache["servers"]:
            self.cache["servers"][server_id]["queue"] = []
            self._save_cache()

    def get_current_song(self, server_id):
        """Отримує поточну пісню."""
        return self.cache["servers"].get(server_id, {}).get("current_song", None)

    def schedule_save(self):
        """Фонове збереження кешу кожні кілька секунд."""
        if time.time() - self.last_save_time > self.save_interval:
            self._save_cache()

# Приклад використання
if __name__ == "__main__":
    cache = EnhancedCache()

    # Додати до черги
    cache.add_to_queue("123456789", "https://www.youtube.com/watch?v=example", "Example Song")

    # Отримати чергу
    print("Queue:", cache.get_queue("123456789"))

    # Отримати наступну пісню
    print("Next song:", cache.get_next_song("123456789"))

    # Очистити чергу
    cache.clear_queue("123456789")
    print("Queue after clearing:", cache.get_queue("123456789"))

