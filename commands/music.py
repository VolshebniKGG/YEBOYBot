


import discord
from discord.ext import commands
from collections import deque
import yt_dlp as youtube_dl
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
import json
import asyncio
import logging
import shutil

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.music_queue = {}
        self.base_path = "data/music"
        os.makedirs(self.base_path, exist_ok=True)

        # Явно задаємо шлях до FFmpeg
        os.environ["FFMPEG_PATH"] = r"E:\Discord Bot\Bot\bin\ffmpeg.exe"
        ffmpeg_path = os.getenv("FFMPEG_PATH", "ffmpeg")

        # Перевірка наявності FFmpeg
        if not os.path.isfile(ffmpeg_path):
            raise FileNotFoundError(f"FFmpeg not found at {ffmpeg_path}. Please ensure the file exists.")
        logging.info(f"FFmpeg found at: {ffmpeg_path}")

        self.ytdl = youtube_dl.YoutubeDL({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "quiet": True,
        })

        self.ffmpeg_options = {"options": "-vn", "executable": ffmpeg_path}

        try:
            self.spotify = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
                client_id=os.getenv("SPOTIPY_CLIENT_ID"),
                client_secret=os.getenv("SPOTIPY_CLIENT_SECRET")
            ))
        except Exception as e:
            logging.error(f"Failed to initialize Spotify credentials: {e}")
            self.spotify = None

    def get_queue_file(self, guild_id):
        return os.path.join(self.base_path, f"{guild_id}_queue.json")

    def load_queue(self, guild_id):
        try:
            with open(self.get_queue_file(guild_id), "r", encoding="utf-8") as f:
                return deque(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return deque()

    def save_queue(self, guild_id, queue):
        try:
            with open(self.get_queue_file(guild_id), "w", encoding="utf-8") as f:
                json.dump(list(queue), f, indent=4)
        except Exception as e:
            logging.error(f"Error saving queue for guild {guild_id}: {e}")

    @commands.command()
    async def play(self, ctx, *, url):
        """Adds a track to the queue or plays it."""
        guild_id = ctx.guild.id
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("❌ You must be in a voice channel to play music!")
            return

        if not ctx.voice_client:
            await ctx.author.voice.channel.connect()

        queue = self.music_queue.setdefault(guild_id, self.load_queue(guild_id))

        if "open.spotify.com" in url:
            if self.spotify:
                await self._handle_spotify_url(ctx, url, queue)
            else:
                await ctx.send("❌ Spotify support is not configured properly.")
        else:
            await self._handle_youtube_url(ctx, url, queue)

        self.save_queue(guild_id, queue)
        if not ctx.voice_client.is_playing():
            await self._play_next(ctx)

    async def _handle_spotify_url(self, ctx, url, queue):
        """Handles Spotify URLs."""
        try:
            if "playlist" in url:
                results = self.spotify.playlist_tracks(url)
                for item in results["items"]:
                    track = item["track"]
                    title = f"{track['name']} - {track['artists'][0]['name']}"
                    search_result = self.ytdl.extract_info(f"ytsearch:{title}", download=False)["entries"][0]
                    queue.append({"title": search_result["title"], "url": search_result["webpage_url"]})
            else:
                result = self.spotify.track(url)
                title = f"{result['name']} - {result['artists'][0]['name']}"
                search_result = self.ytdl.extract_info(f"ytsearch:{title}", download=False)["entries"][0]
                queue.append({"title": search_result["title"], "url": search_result["webpage_url"]})
            await ctx.send("🎵 Tracks from Spotify added to the queue!")
        except Exception as e:
            await ctx.send(f"❌ Error processing Spotify URL: {e}")

    async def _handle_youtube_url(self, ctx, url, queue):
        """Handles YouTube URLs."""
        try:
            info = self.ytdl.extract_info(url, download=False)
            if "entries" in info:
                for entry in info["entries"]:
                    queue.append({"title": entry["title"], "url": entry["url"]})
            else:
                queue.append({"title": info["title"], "url": info["url"]})
            await ctx.send("🎵 Tracks added to the queue!")
        except Exception as e:
            await ctx.send(f"❌ Error processing YouTube URL: {e}")

    async def _play_next(self, ctx):
        """Plays the next track in the queue."""
        guild_id = ctx.guild.id
        queue = self.music_queue.setdefault(guild_id, self.load_queue(guild_id))
        if queue:
            track = queue.popleft()
            self.save_queue(guild_id, queue)
            try:
                ctx.voice_client.play(
                    discord.FFmpegPCMAudio(track["url"], **self.ffmpeg_options),
                    after=lambda e: asyncio.run_coroutine_threadsafe(self._play_next(ctx), self.bot.loop)
                )
                await ctx.send(f"🎶 Now playing: **{track['title']}**")
            except Exception as e:
                await ctx.send(f"❌ Error playing track: {e}")
                logging.error(f"Error playing track: {e}")
        else:
            await ctx.send("✅ The queue is empty.")

    @commands.command()
    async def stop(self, ctx):
        """Stops music and clears the queue."""
        if ctx.voice_client:
            self.music_queue[ctx.guild.id] = deque()
            self.save_queue(ctx.guild.id, deque())
            ctx.voice_client.stop()
            await ctx.send("⏹️ Playback stopped and queue cleared.")
        else:
            await ctx.send("❌ The bot is not in a voice channel!")

    @commands.command()
    async def pause(self, ctx):
        """Pauses playback."""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("⏸️ Playback paused.")
        else:
            await ctx.send("❌ Nothing to pause!")

    @commands.command()
    async def resume(self, ctx):
        """Resumes playback."""
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("▶️ Playback resumed.")
        else:
            await ctx.send("❌ Nothing to resume!")

    @commands.command()
    async def skip(self, ctx):
        """Skips the current track."""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await ctx.send("⏭️ Track skipped.")
        else:
            await ctx.send("❌ Nothing to skip!")

    @commands.command()
    async def queue(self, ctx):
        """Displays the queue."""
        queue = self.music_queue.get(ctx.guild.id, deque())
        if queue:
            await ctx.send("📜 Track queue:\n" + "\n".join([f"{i+1}. {track['title']}" for i, track in enumerate(queue)]))
        else:
            await ctx.send("❌ The queue is empty.")

async def setup(bot):
    logger = logging.getLogger('bot')
    try:
        await bot.add_cog(Music(bot))
        logger.info("Music Cog successfully loaded.")
    except Exception as e:
        logger.error(f"Failed to load Music Cog: {e}")


