


import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
import json
import asyncio
import logging
import time
import configparser

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = {}
        self.data_path = "data/music"
        self.cache_path = "cache/track_cache.json"
        self.semaphore = asyncio.Semaphore(10)
        self.processed_tracks = []  # Список для оброблених треків
        self.cache = self._load_cache()  # Завантажуємо кеш із файлу
        self.unavailable_log_path = "logs/unavailable_videos.json"
        os.makedirs(self.data_path, exist_ok=True)
        os.makedirs("cache", exist_ok=True)

        self.ffmpeg_path = r"E:\Discord Bot\Bot\bin\ffmpeg.exe"  # Абсолютний шлях до ffmpeg
        if not os.path.isfile(self.ffmpeg_path):
            raise FileNotFoundError(f"FFmpeg not found at {self.ffmpeg_path}. Ensure it is installed.")
        logging.info(f"FFmpeg found at: {self.ffmpeg_path}")

        self.ytdl = youtube_dl.YoutubeDL({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "quiet": True,
            "default_search": "auto",
            "extract_flat": False,          # Зменшення кількості обробки плейлистів
            "no_color": True,               # Уникаємо кольорових кодів у помилках
            "ignoreerrors": True,           # Пропуск помилкових треків
            "extractor_retries": 5,         # Додає повторні спроби у разі помилок
            "jsinterp_max_recursion": 50,   # Обмеження рекурсії
        })


        # Створення кешу, якщо він не існує
        if not os.path.exists(self.cache_path):
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({}, f)

    def _load_cache(self):
        """Завантаження кешу треків."""
        try:
            with open(self.cache_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_cache(self):
        """Збереження кешу треків."""
        with open(self.cache_path, "w", encoding="utf-8") as file:
            json.dump(self.cache, file, indent=4)

    async def _get_from_cache(self, key):
        """Перевіряє трек у кеші."""
        cache = await self._load_cache()
        return cache.get(key)

    async def _add_to_cache(self, key, value):
        """Додає трек до кешу."""
        cache = await self._load_cache()
        cache[key] = value
        await self._save_cache(cache)

    async def _process_spotify_track(self, track):
        """Обробка Spotify треку із кешуванням."""
        title = f"{track['name']} - {track['artists'][0]['name']}"
        if title in self.cache:
            logging.info(f"Track found in cache: {title}")
            return self.cache[title]

        try:
            search_result = self.ytdl.extract_info(f"ytsearch:{title}", download=False)["entries"][0]
            track_data = {"title": search_result["title"], "url": search_result["webpage_url"]}
            self.cache[title] = track_data  # Додаємо до кешу
            self._save_cache()
            return track_data
        except Exception as e:
            logging.warning(f"Failed to process Spotify track: {title} - {e}")
            return None


    async def _process_youtube_entry(self, entry):
        """Обробка YouTube треку із кешуванням."""
        track_key = entry.get("url") or entry.get("id")
        if track_key in self.cache:
            logging.info(f"Track found in cache: {track_key}")
            return self.cache[track_key]

        try:
            track_data = {
                "title": entry.get("title", "Unknown"),
                "url": entry.get("url")
            }
            if entry.get("is_live"):
                track_data["title"] += " [Live Stream]"
            self.cache[track_key] = track_data  # Додаємо до кешу
            self._save_cache()
            return track_data
        except Exception as e:
            logging.warning(f"Failed to process YouTube entry: {entry.get('title', 'Unknown')} - {e}")
            return None


        # Читання конфігурації для Spotify
        config = configparser.ConfigParser()
        config.read("config/options.ini")
        client_id = config.get("Spotify", "Client_ID", fallback=None)
        client_secret = config.get("Spotify", "Client_Secret", fallback=None)

        if not client_id or not client_secret:
            raise ValueError("Spotify Client_ID and Client_Secret must be provided in config/options.ini")

        # Ініціалізація Spotify API
        self.spotify = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret
        ))

    def _log_processed_track(self, source, title, elapsed_time):
        """Логування та збереження інформації про оброблений трек."""
        track_info = {
            "source": source,
            "title": title,
            "elapsed_time": elapsed_time
        }
        self.processed_tracks.append(track_info)
        if len(self.processed_tracks) > 5:  # Обмеження на 5 елементів
            self.processed_tracks.pop(0)
        logging.info(f"[{source}] Processed track '{title}' in {elapsed_time:.2f} seconds.")

    def _log_unavailable_videos(self, playlist_url, total, skipped, skipped_titles):
        """Логування пропущених відео у файл."""
        log_data = {
            "playlist_url": playlist_url,
            "total_videos": total,
            "skipped_videos": skipped,
            "skipped_titles": skipped_titles
        }

        # Завантажуємо існуючі дані
        if os.path.exists(self.unavailable_log_path):
            try:
                with open(self.unavailable_log_path, "r", encoding="utf-8") as file:
                    logs = json.load(file)
            except json.JSONDecodeError:
                logs = []
        else:
            logs = []

        # Додаємо новий запис і зберігаємо
        logs.append(log_data)
        with open(self.unavailable_log_path, "w", encoding="utf-8") as file:
            json.dump(logs, file, indent=4)
        logging.info(f"Logged {skipped} unavailable videos from playlist: {playlist_url}")

    def _queue_file(self, guild_id):
        """Отримує шлях до файлу черги для сервера."""
        return os.path.join(self.data_path, f"{guild_id}_queue.json")

    def _load_queue(self, guild_id):
        """Завантажити чергу з файлу."""
        try:
            with open(self._queue_file(guild_id), "r", encoding="utf-8") as file:
                self.queues[guild_id] = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            self.queues[guild_id] = []

    def _save_queue(self, guild_id):
        """Зберегти чергу в файл."""
        os.makedirs(self.data_path, exist_ok=True)
        try:
            with open(self._queue_file(guild_id), "w", encoding="utf-8") as file:
                json.dump(self.queues[guild_id], file, indent=4)
        except Exception as e:
            logging.error(f"Error saving queue for guild {guild_id}: {e}")

    @commands.command()
    async def play(self, ctx, *, query):
        """Add a track or play immediately."""
        guild_id = ctx.guild.id

        # Ensure voice client
        if not ctx.voice_client:
            await ctx.author.voice.channel.connect()

        # Load queue
        if guild_id not in self.queues:
            self._load_queue(guild_id)

        # Handle query
        if "spotify.com" in query:
            await self._handle_spotify(ctx, query, guild_id)
        else:
            await self._handle_youtube(ctx, query, guild_id)

        # Save queue and play if idle
        self._save_queue(guild_id)
        if not ctx.voice_client.is_playing():
            await self._play_next(ctx)

    @commands.command()
    async def play_playlist(self, ctx, *, url):
        """Команда для відтворення плейлисту."""
        guild_id = ctx.guild.id

        # Ensure voice client
        if not ctx.voice_client:
            await ctx.author.voice.channel.connect()

        # Handle URL
        if "spotify.com" in url:
            await self._handle_spotify(ctx, url, guild_id)
        else:
            await self._handle_youtube(ctx, url, guild_id)

        # Play if idle
        if not ctx.voice_client.is_playing():
            await self._play_next(ctx)

    async def _handle_spotify(self, ctx, query, guild_id):
        """Handles Spotify URLs, including playlists and individual tracks."""
        try:
            semaphore = asyncio.Semaphore(5)

            async def process_track(track):
                start_time = time.time()
                async with semaphore:
                    title = f"{track['name']} - {track['artists'][0]['name']}"
                    search_result = self.ytdl.extract_info(f"ytsearch:{title}", download=False)["entries"][0]
                    elapsed_time = time.time() - start_time
                    self._log_processed_track("Spotify", title, elapsed_time)
                    return {"title": search_result["title"], "url": search_result["webpage_url"]}

            if "playlist" in query:
                results = self.spotify.playlist_tracks(query)
                tasks = [process_track(item["track"]) for item in results["items"]]
                tracks = await asyncio.gather(*tasks)
                self.queues[guild_id].extend(tracks)
            else:
                track = self.spotify.track(query)
                processed_track = await process_track(track)
                self.queues[guild_id].append(processed_track)

            await ctx.send("🎵 Tracks from Spotify added to the queue!")
        except Exception as e:
            await ctx.send(f"❌ Spotify error: {e}")
        
    async def _handle_youtube(self, ctx, query, guild_id):
        """Handles YouTube queries or playlists with error handling."""
        try:
            semaphore = asyncio.Semaphore(10)
            added_count = 0
            skipped_count = 0
            skipped_titles = []

            async def process_entry(entry):
                """Processes a single track entry with timeout."""
                nonlocal skipped_count, skipped_titles, added_count
                async with semaphore:
                    try:
                        # Пропускаємо недоступні відео
                        if entry.get("availability") != "public":
                            skipped_count += 1
                            skipped_titles.append(entry.get("title", "Unknown"))
                            return None
                        # Додаємо підтримку прямих трансляцій
                        if entry.get("is_live"):
                            return {
                                "title": f"{entry['title']} [Live Stream]",
                                "url": entry["url"]
                            }
                        # Звичайні треки
                        return {"title": entry["title"], "url": entry["url"]}
                    except Exception as e:
                        skipped_count += 1
                        skipped_titles.append(entry.get("title", "Unknown"))
                        logging.warning(f"Skipping video: {entry.get('title', 'Unknown')} - {e}")
                        return None

            info = self.ytdl.extract_info(query, download=False)
            if "entries" in info:
                entries = info["entries"]
                tasks = [process_entry(entry) for entry in entries]
                tracks = await asyncio.gather(*tasks, return_exceptions=True)
                available_tracks = [track for track in tracks if track]
                self.queues[guild_id].extend(available_tracks)
                added_count += len(available_tracks)
            else:
                # Обробка одного треку
                track = await process_entry(info)
                if track:
                    self.queues[guild_id].append(track)
                    added_count += 1

            if added_count > 0:
                await ctx.send(f"🎵 Added {added_count} tracks. Skipped {skipped_count} unavailable tracks.")
            else:
                await ctx.send(f"❌ No playable tracks found. Skipped {skipped_count} videos.")
        except Exception as e:
            await ctx.send(f"❌ Error processing YouTube playlist: {e}")


    async def _play_next(self, ctx):
        """Plays the next track in the queue."""
        guild_id = ctx.guild.id
        if not self.queues.get(guild_id):
            await ctx.send("✅ Queue is empty! Add more tracks to play.")
            return

        track = self.queues[guild_id].pop(0)
        self._save_queue(guild_id)

        try:
            ctx.voice_client.play(
                discord.FFmpegPCMAudio(track["url"], executable=self.ffmpeg_path),
                after=lambda e: asyncio.run_coroutine_threadsafe(self._play_next(ctx), self.bot.loop)
            )
            await ctx.send(f"🎶 Now playing: **{track['title']}**")
        except Exception as e:
            await ctx.send(f"❌ Error playing track: {e}. Skipping to the next one...")
            await self._play_next(ctx)

    @commands.command()
    async def stop(self, ctx):
        """Stops music and clears the queue."""
        guild_id = ctx.guild.id
        if ctx.voice_client:
            self.queues[guild_id] = []  # Очищення черги
            self._save_queue(guild_id)  # Збереження оновленої черги
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
        guild_id = ctx.guild.id
        queue = self.queues.get(guild_id, [])
        if queue:
            displayed_queue = queue[:10]  # Відображаємо максимум 10 треків
            message = "📜 **Track Queue:**\n" + "\n".join(
                [f"{i+1}. {track['title']}" for i, track in enumerate(displayed_queue)]
            )
            if len(queue) > 10:
                message += f"\n...and {len(queue) - 10} more tracks."
            await ctx.send(message)
        else:
            await ctx.send("❌ The queue is empty.")

async def setup(bot):
    logger = logging.getLogger('bot')
    try:
        await bot.add_cog(Music(bot))
        logger.info("Music Cog successfully loaded.")
    except Exception as e:
        logger.error(f"Failed to load Music Cog: {e}")
        raise  # Повторне підняття помилки для повідомлення про критичну проблему


