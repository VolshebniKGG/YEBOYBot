

import youtube_dl
import discord
from discord.ext import commands
import logging

logger = logging.getLogger('bot')

class YouTubeAPI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def search_video(self, query):
        """Search for a video on YouTube based on a query."""
        logger.info(f"Searching for video: {query}")
        with youtube_dl.YoutubeDL({
            'quiet': True,
            'format': 'bestaudio/best',
            'noplaylist': True,
        }) as ydl:
            try:
                results = ydl.extract_info(f"ytsearch:{query}", download=False)
                video = results['entries'][0]
                return {
                    'title': video['title'],
                    'url': video['webpage_url'],
                    'duration': video['duration']
                }
            except Exception as e:
                logger.error(f"Error searching video: {e}")
                return None

    async def get_video_info(self, url):
        """Retrieve information about a specific video by URL."""
        logger.info(f"Fetching video info for URL: {url}")
        with youtube_dl.YoutubeDL({
            'quiet': True,
            'format': 'bestaudio/best',
        }) as ydl:
            try:
                video_info = ydl.extract_info(url, download=False)
                return {
                    'title': video_info['title'],
                    'url': video_info['webpage_url'],
                    'duration': video_info['duration']
                }
            except Exception as e:
                logger.error(f"Error fetching video info: {e}")
                return None

async def setup(bot):
    """Asynchronous setup function to add the cog to the bot."""
    await bot.add_cog(YouTubeAPI(bot))


