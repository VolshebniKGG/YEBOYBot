

import discord
from discord.ext import commands
import logging
import yt_dlp as youtube_dl

logger = logging.getLogger('bot')

class YouTubeAPI(commands.Cog):
    """
    Ког для взаємодії з YouTube через yt-dlp.
    Пошук відео, отримання інформації тощо.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Параметри для yt-dlp
        self.ydl_opts = {
            "quiet": True,
            "format": "bestaudio/best",
            "noplaylist": True,          # Беремо лише одне відео, якщо передано пошуковий запит
            "default_search": "auto",    # Дозволяє використовувати 'ytsearch:'
            "ignoreerrors": True,
            "nocheckcertificate": True,
        }

    def search_video(self, query: str) -> dict | None:
        """
        Здійснює пошук одного відео на YouTube за рядком запиту.
        Повертає словник з title, url, duration або None у разі помилки.
        """
        logger.info(f"Searching for video: {query}")
        with youtube_dl.YoutubeDL(self.ydl_opts) as ydl:
            try:
                results = ydl.extract_info(f"ytsearch:{query}", download=False)
                if not results or "entries" not in results or not results["entries"]:
                    logger.warning(f"No entries found for query: {query}")
                    return None

                video = results["entries"][0]
                return {
                    "title": video["title"],
                    "url": video["webpage_url"],
                    "duration": video.get("duration", 0)
                }
            except Exception as e:
                logger.error(f"Error searching video: {e}")
                return None

    def get_video_info(self, url: str) -> dict | None:
        """
        Отримує інформацію про конкретне відео за URL.
        Повертає словник з title, url, duration або None у разі помилки.
        """
        logger.info(f"Fetching video info for URL: {url}")
        with youtube_dl.YoutubeDL(self.ydl_opts) as ydl:
            try:
                video_info = ydl.extract_info(url, download=False)
                if not video_info:
                    logger.warning(f"No info found for URL: {url}")
                    return None

                return {
                    "title": video_info["title"],
                    "url": video_info["webpage_url"],
                    "duration": video_info.get("duration", 0)
                }
            except Exception as e:
                logger.error(f"Error fetching video info: {e}")
                return None

# Для py-cord метод add_cog(...) — синхронний, тож не використовуємо await.
def setup(bot: commands.Bot):
    """
    Підключення Cog до бота. 
    """
    bot.add_cog(YouTubeAPI(bot))
    logger.info("YouTubeAPI Cog successfully loaded.")



