import discord
from discord.ext import commands
import logging
import yt_dlp as youtube_dl
import asyncio

# Налаштування логування: повідомлення будуть виводитися в консоль.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s:%(levelname)s:%(name)s: %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('bot')


class YouTubeAPI(commands.Cog):
    """
    Cog для взаємодії з YouTube через yt-dlp.
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

    def _search_video_sync(self, query: str) -> dict | None:
        """
        Синхронна функція для пошуку відео.
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

    async def search_video(self, query: str) -> dict | None:
        """
        Асинхронно здійснює пошук відео, викликаючи синхронну функцію у окремому потоці.
        """
        return await asyncio.to_thread(self._search_video_sync, query)

    def _get_video_info_sync(self, url: str) -> dict | None:
        """
        Синхронна функція для отримання інформації про відео.
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

    async def get_video_info(self, url: str) -> dict | None:
        """
        Асинхронно отримує інформацію про відео, викликаючи синхронну функцію у окремому потоці.
        """
        return await asyncio.to_thread(self._get_video_info_sync, url)


def setup(bot: commands.Bot):
    """
    Підключення Cog до бота.
    """
    bot.add_cog(YouTubeAPI(bot))
    logger.info("YouTubeAPI Cog successfully loaded.")
