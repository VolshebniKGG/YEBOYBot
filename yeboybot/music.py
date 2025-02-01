


import os
import json
import time
import asyncio
import logging
import configparser

import discord
from discord.ext import commands

import yt_dlp as youtube_dl
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

logger = logging.getLogger("bot")

class Music(commands.Cog):
    """
    Ког для відтворення музики з YouTube/Spotify, з кешуванням треків і записом у чергу.
    Працює з py-cord[voice].
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queues = {}  # {guild_id: [ {title, url}, ... ]}
        self.data_path = "data/music"
        self.queue_path = "data/queues"
        self.cache_path = os.path.join(self.data_path, "cache.json")
        self.track_cache_path = os.path.join(self.data_path, "track_cache.json")
        self.unavailable_log_path = os.path.join(self.data_path, "unavailable_log.json")
        self.processed_tracks = []  # Логовані треки (історія 5 треків)

        # Переконуємося, що папки існують
        os.makedirs(self.data_path, exist_ok=True)
        os.makedirs(self.queue_path, exist_ok=True)

        # Завантаження кешу
        self.cache = self._load_cache()

        # Шляхи до ffmpeg та ffprobe
        self.ffmpeg_path = r"E:\Discord Bot\Bot\bin\ffmpeg.exe"
        self.ffprobe_path = r"E:\Discord Bot\Bot\bin\ffprobe.exe"

        # Перевірка існування ffmpeg
        if not os.path.isfile(self.ffmpeg_path):
            raise FileNotFoundError(f"FFmpeg не знайдено за адресою: {self.ffmpeg_path}")
        logger.info(f"FFmpeg знайдено: {self.ffmpeg_path}")

        # Перевірка існування ffprobe
        if not os.path.isfile(self.ffprobe_path):
            raise FileNotFoundError(f"FFprobe не знайдено за адресою: {self.ffprobe_path}")
        logger.info(f"FFprobe знайдено: {self.ffprobe_path}")

        # Налаштування yt-dlp із вказаним шляхом до ffmpeg
        self.ytdl = youtube_dl.YoutubeDL({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "quiet": True,
            "default_search": "auto",
            "noplaylist": False,
            "extract_flat": False,
            "ignoreerrors": True,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "extractor_retries": 3,
            "ffmpeg_location": self.ffmpeg_path,  # Використовуємо наш шлях
        })

        # Якщо файлу кешу немає — створимо пустий
        if not os.path.exists(self.cache_path):
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({}, f)

        # Зчитуємо Spotify-креденшли з config/options.ini
        config = configparser.ConfigParser()
        config.read("config/options.ini")
        client_id = config.get("Spotify", "Client_ID", fallback=None)
        client_secret = config.get("Spotify", "Client_Secret", fallback=None)
        if not client_id or not client_secret:
            raise ValueError("Spotify Client_ID і Client_Secret мають бути вказані у config/options.ini")

        # Ініціалізація Spotify API
        self.spotify = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret
            )
        )

    def _load_cache(self) -> dict:
        """Завантажує JSON-кеш треків із track_cache_path."""
        try:
            with open(self.track_cache_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Cache file not found or corrupted. Using empty cache.")
            return {}

    def _save_cache(self) -> None:
        """Зберігає JSON-кеш треків до track_cache_path."""
        try:
            with open(self.track_cache_path, "w", encoding="utf-8") as file:
                json.dump(self.cache, file, indent=4)
        except Exception as e:
            logger.error(f"Error saving track cache: {e}")

    def _queue_file(self, guild_id: int) -> str:
        """Повертає шлях до файлу черги для заданого сервера."""
        return os.path.join(self.queue_path, f"{guild_id}_queue.json")

    def _load_queue(self, guild_id: int) -> list:
        """
        Завантажує чергу серверу з файлу. Якщо файл відсутній або пошкоджений — повертає [].
        """
        path = self._queue_file(guild_id)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"Queue file {path} is corrupted. Using empty queue.")
                return []
        return []

    def _save_queue(self, guild_id: int, queue: list) -> None:
        """Зберігає чергу (list) до JSON-файлу guild_id."""
        path = self._queue_file(guild_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(queue, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving queue for guild {guild_id}: {e}")

    @commands.command(help="Додати трек або пошуковий запит у чергу та відтворити.")
    async def play(self, ctx: commands.Context, *, query: str):
        guild_id = ctx.guild.id

        # Перевірка підключення користувача до голосового каналу
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("❌ Спочатку приєднайтесь до голосового каналу.")
            return

        if not ctx.voice_client:
            await ctx.author.voice.channel.connect()
        elif ctx.voice_client.channel != ctx.author.voice.channel:
            await ctx.voice_client.move_to(ctx.author.voice.channel)

        # Ініціалізуємо чергу, якщо вона відсутня
        if guild_id not in self.queues:
            self.queues[guild_id] = self._load_queue(guild_id)

        try:
            if "spotify.com" in query:
                await self._handle_spotify(ctx, query, guild_id)
            else:
                await self._handle_youtube(ctx, query, guild_id)

            # Збережемо чергу
            self._save_queue(guild_id, self.queues[guild_id])

            # Якщо не грає — запускаємо наступний трек
            if not ctx.voice_client.is_playing():
                await self._play_next(ctx)
        except Exception as e:
            logger.error(f"Error in play command with query '{query}': {e}", exc_info=True)
            await ctx.send(f"❌ Помилка при обробці запиту: {e}")

    @commands.command(help="Відтворити цілий плейлист (Spotify/YouTube).")
    async def play_playlist(self, ctx: commands.Context, *, url: str):
        guild_id = ctx.guild.id

        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("❌ Спочатку приєднайтесь до голосового каналу.")
            return

        if not ctx.voice_client:
            await ctx.author.voice.channel.connect()
        elif ctx.voice_client.channel != ctx.author.voice.channel:
            await ctx.voice_client.move_to(ctx.author.voice.channel)

        if guild_id not in self.queues:
            self.queues[guild_id] = self._load_queue(guild_id)

        try:
            if "spotify.com" in url:
                await self._handle_spotify(ctx, url, guild_id)
            else:
                await self._handle_youtube(ctx, url, guild_id)

            if not ctx.voice_client.is_playing():
                await self._play_next(ctx)
        except Exception as e:
            logger.error(f"Error in play_playlist with url '{url}': {e}", exc_info=True)
            await ctx.send(f"❌ Помилка при обробці плейлиста: {e}")

    async def _handle_spotify(self, ctx: commands.Context, query: str, guild_id: int):
        """
        Обробка Spotify URL (плейлист чи окремий трек), додавання у чергу.
        """
        logger.debug(f"_handle_spotify called with query: {query}")
        try:
            if "playlist" in query:
                results = self.spotify.playlist_tracks(query)
                items = results.get("items", [])
                for item in items:
                    track_obj = item.get("track")
                    if track_obj:
                        processed = await self._process_spotify_track(track_obj)
                        if processed:
                            self.queues[guild_id].append(processed)
                await ctx.send("🎵 Успішно додано треки зі Spotify-плейлиста в чергу.")
            else:
                track_obj = self.spotify.track(query)
                processed_track = await self._process_spotify_track(track_obj)
                if processed_track:
                    self.queues[guild_id].append(processed_track)
                    await ctx.send(f"🎵 Додано трек зі Spotify: **{processed_track['title']}**")
        except Exception as e:
            logger.error(f"Error processing Spotify link '{query}': {e}", exc_info=True)
            await ctx.send(f"❌ Spotify error: {e}")

    async def _process_spotify_track(self, track_obj: dict) -> dict | None:
        """
        Обробка одного Spotify-треку:
        - Формуємо назву (track_name + artist).
        - Шукаємо через yt-dlp (ytsearch).
        - Зберігаємо в cache.
        """
        try:
            title_search = f"{track_obj['name']} - {track_obj['artists'][0]['name']}"
            if title_search in self.cache:
                logger.info(f"Трек вже в кеші: {title_search}")
                return self.cache[title_search]

            info = self.ytdl.extract_info(f"ytsearch:{title_search}", download=False)
            if info and "entries" in info and info["entries"]:
                best_entry = info["entries"][0]
                track_data = {
                    "title": best_entry.get("title", "Unknown"),
                    "url": best_entry.get("webpage_url", "")
                }
                self.cache[title_search] = track_data
                self._save_cache()
                return track_data
            else:
                logger.warning(f"Не знайдено результату для Spotify-треку: {title_search}")
                return None
        except Exception as e:
            logger.warning(f"Spotify-трек '{track_obj.get('name','Unknown')}' обробити не вдалося: {e}")
            return None

    async def _handle_youtube(self, ctx: commands.Context, query: str, guild_id: int):
        """
        Обробка YouTube посилання чи пошукового запиту.
        Може бути плейлист чи окреме відео.
        """
        logger.debug(f"_handle_youtube called with query: {query}")
        try:
            info = self.ytdl.extract_info(query, download=False)
            if info and "entries" in info and info["entries"]:
                entries = info["entries"]
                added_count = 0
                skipped_count = 0

                for e in entries:
                    # Якщо недоступне, пропускаємо
                    if e.get("availability") != "public":
                        skipped_count += 1
                        continue
                    # Використовуємо webpage_url, якщо він є, інакше fallback до url
                    track_url = e.get("webpage_url", e.get("url"))
                    track_data = {
                        "title": e.get("title", "Unknown"),
                        "url": track_url
                    }
                    if e.get("is_live"):
                        track_data["title"] += " [Live Stream]"
                    self.queues[guild_id].append(track_data)
                    added_count += 1

                await ctx.send(f"🎵 Додано {added_count} трек(ів). Пропущено: {skipped_count}.")
            else:
                # Обробка одного треку
                if info is None:
                    await ctx.send("❌ Не вдалося знайти/відтворити цей запит.")
                    return

                if info.get("availability") != "public":
                    await ctx.send("❌ Це відео недоступне або приватне.")
                    return

                title = info.get("title", "Unknown")
                # Для одного треку намагаємося отримати webpage_url
                url = info.get("webpage_url") or info.get("url", "")
                track_data = {"title": title, "url": url}
                self.queues[guild_id].append(track_data)
                await ctx.send(f"🎵 Додано трек: **{title}**")

        except Exception as e:
            logger.error(f"Помилка обробки YouTube-запиту '{query}': {e}", exc_info=True)
            await ctx.send(f"❌ YouTube error: {e}")

    async def _play_next(self, ctx: commands.Context):
        """
        Програє наступний трек. Якщо черга порожня — повідомляє про це.
        """
        guild_id = ctx.guild.id
        queue_ = self.queues.get(guild_id, [])
        if not queue_:
            logger.debug(f"Черга порожня для guild_id={guild_id}.")
            await ctx.send("✅ Queue is empty! Add more tracks to play.")
            return

        track = queue_.pop(0)
        self._save_queue(guild_id, queue_)
        title = track.get("title", "Unknown")
        url = track.get("url", "")

        # Перевірка voice_client
        if not ctx.voice_client:
            logger.debug("voice_client is None, не можемо програти трек.")
            if ctx.author.voice and ctx.author.voice.channel:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.send("❌ Немає активного голосового підключення.")
                return

        ffmpeg_options = "-vn"  # відключаємо відео
        try:
            # Отримуємо дані з youtube-dl для отримання прямого URL аудіопотоку
            data = self.ytdl.extract_info(url, download=False)
            stream_url = data.get("url")
            # Якщо це live stream – додаємо reconnect параметри
            if data.get("is_live"):
                before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
            else:
                before_options = ""
            source = discord.FFmpegPCMAudio(
                stream_url,
                executable=self.ffmpeg_path,
                before_options=before_options,
                options=ffmpeg_options
            )
        except Exception as e:
            logger.error(f"Error creating FFmpegPCMAudio для треку {title}: {e}", exc_info=True)
            await ctx.send(f"❌ Не вдалося створити аудіо-джерело для: {title}")
            # Якщо сталася помилка, переходимо до наступного треку
            await self._play_next(ctx)
            return

        def after_playing(error):
            if error:
                logger.error(f"Error after playing track '{title}': {error}", exc_info=True)
            else:
                logger.debug(f"Трек '{title}' відтворився без помилок.")
            # Запускаємо наступний трек у головному циклі
            asyncio.run_coroutine_threadsafe(self._play_next(ctx), self.bot.loop)

        try:
            ctx.voice_client.play(source, after=after_playing)
            await ctx.send(f"🎶 Now playing: **{title}**")
        except Exception as e:
            logger.error(f"Error playing track '{title}': {e}", exc_info=True)
            await ctx.send(f"❌ Error playing track: {title}. Перехід до наступного...")
            await self._play_next(ctx)


    @commands.command(help="Зупинити музику і очистити чергу.")
    async def stop(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        if ctx.voice_client:
            self.queues[guild_id] = []
            self._save_queue(guild_id, [])
            ctx.voice_client.stop()
            await ctx.send("⏹️ Playback stopped and queue cleared.")
        else:
            await ctx.send("❌ The bot is not in a voice channel!")

    @commands.command(help="Призупинити відтворення.")
    async def pause(self, ctx: commands.Context):
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("⏸️ Playback paused.")
        else:
            await ctx.send("❌ Nothing to pause!")

    @commands.command(help="Продовжити відтворення.")
    async def resume(self, ctx: commands.Context):
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("▶️ Playback resumed.")
        else:
            await ctx.send("❌ Nothing to resume!")

    @commands.command(help="Пропустити поточний трек.")
    async def skip(self, ctx: commands.Context):
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await ctx.send("⏭️ Track skipped.")
        else:
            await ctx.send("❌ Nothing to skip!")

    @commands.command(help="Показати перелік треків у черзі.")
    async def queue(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        current_queue = self.queues.get(guild_id, [])
        if current_queue:
            displayed = current_queue[:10]
            lines = ["📜 **Track Queue:**"]
            for i, trk in enumerate(displayed, start=1):
                lines.append(f"{i}. {trk['title']}")
            if len(current_queue) > 10:
                lines.append(f"...and {len(current_queue) - 10} more tracks.")
            await ctx.send("\n".join(lines))
        else:
            await ctx.send("❌ The queue is empty.")

def setup(bot: commands.Bot):
    """
    Підключення Music Cog до бота (py-cord).
    Оскільки add_cog є синхронним, ми не використовуємо await.
    """
    try:
        bot.add_cog(Music(bot))
        logger.info("Music Cog successfully loaded.")
    except Exception as e:
        logger.error(f"Failed to load Music Cog: {e}", exc_info=True)
        raise








