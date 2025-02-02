


import os
import json
import time
import logging
from typing import Dict, Any, Optional, List

import discord
from discord.ext import commands

# Налаштування логування: повідомлення будуть виводитись у консоль.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("bot")


class EnhancedCache:
    """
    Клас для керування кешем пісень (чергою) у JSON-файлі
    з автоматичним резервним копіюванням і видаленням старих бекапів.
    """
    def __init__(
        self,
        file_path: str = "cache/song_queue.json",
        backup_path: str = "cache/backup/",
        save_interval: float = 60.0,
        backup_retention_days: int = 7
    ) -> None:
        """
        :param file_path: Шлях до основного JSON-файлу кешу
        :param backup_path: Шлях до папки з резервними копіями
        :param save_interval: Інтервал (у секундах) між автозбереженнями
        :param backup_retention_days: Скільки діб зберігати резервні копії
        """
        self.file_path = file_path
        self.backup_path = backup_path
        self.cache: Dict[str, Any] = {}
        self.last_save_time = 0.0
        self.save_interval = save_interval
        self.backup_retention_time = backup_retention_days * 24 * 3600
        self._load_cache()

    def _load_cache(self) -> None:
        """Завантажити дані з JSON у self.cache. Якщо файл відсутній, створюємо базову структуру."""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, "r", encoding="utf-8") as file:
                    self.cache = json.load(file)
                logger.info(f"Дані успішно завантажено з {self.file_path}")
            else:
                self.cache = {"servers": {}}
                self._save_cache(force=True)
        except json.JSONDecodeError:
            logger.error(f"Файл {self.file_path} пошкоджено. Використовуємо порожній кеш.")
            self.cache = {"servers": {}}
            self._save_cache(force=True)

    def _save_cache(self, force: bool = False) -> None:
        """
        Зберегти кеш у файл з урахуванням інтервалу.
        Робить резервну копію попереднього стану, а також прибирає старі бекапи.
        """
        current_time = time.time()
        if not force and (current_time - self.last_save_time < self.save_interval):
            return

        # Створюємо папки, якщо вони не існують
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        os.makedirs(self.backup_path, exist_ok=True)

        # Видаляємо старі бекапи перед створенням нового
        self._clean_old_backups()

        # Робимо бекап поточного файлу (якщо він існує)
        if os.path.exists(self.file_path):
            backup_file = os.path.join(
                self.backup_path,
                f"song_queue_backup_{int(current_time)}.json"
            )
            os.replace(self.file_path, backup_file)
            logger.info(f"Резервну копію створено: {backup_file}")

        # Записуємо нові дані
        try:
            with open(self.file_path, "w", encoding="utf-8") as file:
                json.dump(self.cache, file, indent=4)
            self.last_save_time = current_time
            logger.info(f"Дані збережено у {self.file_path}")
        except Exception as e:
            logger.error(f"Помилка запису файлу {self.file_path}: {e}")

    def _clean_old_backups(self) -> None:
        """Видалити бекапи, вік яких перевищує self.backup_retention_time."""
        current_time = time.time()
        if not os.path.exists(self.backup_path):
            return

        for backup_file in os.listdir(self.backup_path):
            backup_file_path = os.path.join(self.backup_path, backup_file)
            if os.path.isfile(backup_file_path):
                file_age = current_time - os.path.getmtime(backup_file_path)
                if file_age > self.backup_retention_time:
                    os.remove(backup_file_path)
                    logger.info(f"Стару резервну копію видалено: {backup_file_path}")

    def add_to_queue(self, server_id: str, url: str, title: Optional[str] = None) -> None:
        """
        Додати пісню (url і, опційно, title) до черги сервера server_id.
        """
        if "servers" not in self.cache:
            self.cache["servers"] = {}
        if server_id not in self.cache["servers"]:
            self.cache["servers"][server_id] = {"current_song": None, "queue": []}

        self.cache["servers"][server_id]["queue"].append({
            "url": url,
            "title": title or url
        })
        logger.info(f"Додано пісню до черги сервера {server_id}: {title or url}")
        self._save_cache()

    def get_queue(self, server_id: str) -> List[Dict[str, str]]:
        """
        Повертає список треків у черзі сервера server_id.
        Якщо нічого не знайдено, повертає пустий список.
        """
        return self.cache.get("servers", {}).get(server_id, {}).get("queue", [])

    def get_next_song(self, server_id: str) -> Optional[Dict[str, str]]:
        """
        Витягує перший трек з черги та робить його поточним.
        Повертає словник з полями 'url' і 'title', або None, якщо черга пуста.
        """
        server_data = self.cache.get("servers", {}).get(server_id)
        if server_data and server_data["queue"]:
            next_song = server_data["queue"].pop(0)
            server_data["current_song"] = next_song
            logger.info(f"Переміщено наступну пісню в current_song для сервера {server_id}: {next_song}")
            self._save_cache()
            return next_song
        return None

    def clear_queue(self, server_id: str) -> None:
        """Очищає чергу сервера (не чіпаючи 'current_song')."""
        server_data = self.cache.get("servers", {}).get(server_id)
        if server_data:
            server_data["queue"] = []
            logger.info(f"Черга очищена для сервера {server_id}")
            self._save_cache()

    def get_current_song(self, server_id: str) -> Optional[Dict[str, str]]:
        """Повертає поточний трек сервера server_id або None."""
        return self.cache.get("servers", {}).get(server_id, {}).get("current_song", None)

    def schedule_save(self) -> None:
        """
        Викликається періодично (наприклад, у фоні), щоб перевірити,
        чи не настав час зберегти кеш.
        """
        if (time.time() - self.last_save_time) > self.save_interval:
            self._save_cache()


class SongQueueHandler(commands.Cog):
    """
    Простий приклад Cog, який може використовувати EnhancedCache
    для керування чергою серверів. Демонструє, як інтегрувати кеш у py-cord.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Ініціалізуємо наш кеш
        self.cache = EnhancedCache(file_path="cache/song_queue.json")
        logger.info("SongQueueHandler ініціалізовано.")

    @commands.command(name="sq_add", help="Додати пісню до черги (демо-команда).")
    async def sq_add(self, ctx: commands.Context, url: str, *, title: str = None):
        """
        Додати URL (і опційний заголовок) до черги поточного сервера.
        Приклад використання:
        !sq_add https://youtube.com/... "My Song"
        """
        server_id = str(ctx.guild.id)
        self.cache.add_to_queue(server_id, url, title)
        await ctx.send(f"Додано до черги: {title or url}")

    @commands.command(name="sq_next", help="Отримати наступну пісню з черги (демо-команда).")
    async def sq_next(self, ctx: commands.Context):
        """
        Витягує наступний трек з черги і відображає його назву.
        """
        server_id = str(ctx.guild.id)
        next_song = self.cache.get_next_song(server_id)
        if next_song:
            await ctx.send(f"Наступний трек: {next_song['title']}")
        else:
            await ctx.send("Черга пуста.")

    @commands.command(name="sq_clear", help="Очистити чергу (демо-команда).")
    async def sq_clear(self, ctx: commands.Context):
        """
        Очищає чергу для сервера.
        """
        server_id = str(ctx.guild.id)
        self.cache.clear_queue(server_id)
        await ctx.send("Черга успішно очищена.")

    @commands.command(name="sq_current", help="Показати поточну пісню (демо-команда).")
    async def sq_current(self, ctx: commands.Context):
        """
        Відображає поточний трек, якщо є.
        """
        server_id = str(ctx.guild.id)
        current = self.cache.get_current_song(server_id)
        if current:
            await ctx.send(f"Зараз грає: {current['title']}")
        else:
            await ctx.send("Наразі не відтворюється жодного треку.")


def setup(bot: commands.Bot):
    """
    Функція підключення Cog до бота (py-cord).
    Викликається, коли ви робите: bot.load_extension('song_queue_handler')
    """
    bot.add_cog(SongQueueHandler(bot))
    logger.info("SongQueueHandler Cog loaded.")


