


import os
import pathlib
import json
import asyncio
import logging
import configparser
import random
from typing import Any, Dict, List, Optional

import discord
from discord.ext import commands

import yt_dlp as youtube_dl
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from yeboybot.autoplaylist import AutoPlaylistManager
from yeboybot.ytdlp_oauth2_plugin import YouTubeOAuth2Handler

# Налаштування логування: повідомлення виводитимуться в консоль.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("bot")

# Константи для обробки чанків
CHUNK_SIZE = 25
CHUNK_DELAY = 0.1


# =================================================================
# Клас для пагінації черги треків за допомогою кнопок (discord.ui.View)
# =================================================================
class QueueView(discord.ui.View):
    """View для перегляду (пагінації) черги треків."""
    def __init__(self, ctx: commands.Context, queue: List[Dict[str, Any]], items_per_page: int = 10):
        super().__init__(timeout=60)  # Активність View – 60 секунд
        self.ctx = ctx
        self.queue = queue
        self.items_per_page = items_per_page
        self.current_page = 0

    def get_page_count(self) -> int:
        return (len(self.queue) - 1) // self.items_per_page + 1

    def get_embed(self) -> discord.Embed:
        embed = discord.Embed(title="📜 Черга треків")
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        page_items = self.queue[start:end]
        if page_items:
            description = "\n".join(
                f"{start + i + 1}. {track.get('title', 'Unknown')}"
                for i, track in enumerate(page_items)
            )
            embed.description = description
        else:
            embed.description = "Черга порожня."
        embed.set_footer(text=f"Сторінка {self.current_page + 1}/{self.get_page_count()}")
        return embed

    @discord.ui.button(label="⏪", style=discord.ButtonStyle.primary, custom_id="first_page")
    async def first_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        self.current_page = 0
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary, custom_id="previous_page")
    async def previous_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary, custom_id="next_page")
    async def next_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.current_page < self.get_page_count() - 1:
            self.current_page += 1
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="⏩", style=discord.ButtonStyle.primary, custom_id="last_page")
    async def last_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        self.current_page = self.get_page_count() - 1
        await interaction.response.edit_message(embed=self.get_embed(), view=self)


# =================================================================
# Music Cog
# =================================================================
class Music(commands.Cog):
    """
    Cog для відтворення музики з YouTube/Spotify із розширеним функціоналом,
    кешуванням треків, керуванням чергою, регулюванням гучності та автовідключенням.
    
    Використовує py-cord[voice], ffmpeg, yt-dlp та spotipy.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # --- Налаштування автосписків ---
        # Можна завантажити налаштування з файлу конфігурації
        self.config = type("Config", (), {})()  # Простий об'єкт для зберігання налаштувань
        self.config.auto_playlist_dir = pathlib.Path("data/apl")
        self.config.enable_queue_history_global = True

        # Менеджер автосписків
        self.apl_manager = AutoPlaylistManager(self)

        # Усі черги зберігаються у пам'яті: {guild_id: [ {title, url}, ... ]}
        self.queues: Dict[int, List[Dict[str, Any]]] = {}
        # Дані про поточний трек, що грає: {guild_id: {title, duration}}
        self.current_tracks: Dict[int, Optional[Dict[str, Any]]] = {}

        # Шляхи до даних
        self.data_path = "data/music"
        self.queue_path = "data/queues"
        self.cache_path = os.path.join(self.data_path, "cache.json")
        self.track_cache_path = os.path.join(self.data_path, "track_cache.json")
        self.unavailable_log_path = os.path.join(self.data_path, "unavailable_log.json")

        self.processed_tracks: List[Any] = []  # Лог останніх оброблених треків
        self.default_volume = 0.5   # Початкова гучність (50%)

        # Створення необхідних тек
        os.makedirs(self.data_path, exist_ok=True)
        os.makedirs(self.queue_path, exist_ok=True)

        # Завантаження кешу треків
        self.cache = self._load_cache()

        # Шляхи до ffmpeg та ffprobe (налаштуйте під свої умови)
        self.ffmpeg_path = r"E:\Discord Bot\Bot\bin\ffmpeg.exe"
        self.ffprobe_path = r"E:\Discord Bot\Bot\bin\ffprobe.exe"

        if not os.path.isfile(self.ffmpeg_path):
            raise FileNotFoundError(f"FFmpeg не знайдено за адресою: {self.ffmpeg_path}")
        logger.info(f"FFmpeg знайдено: {self.ffmpeg_path}")

        if not os.path.isfile(self.ffprobe_path):
            raise FileNotFoundError(f"FFprobe не знайдено за адресою: {self.ffprobe_path}")
        logger.info(f"FFprobe знайдено: {self.ffprobe_path}")

        # Налаштування yt-dlp
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
            "ffmpeg_location": self.ffmpeg_path,
        })

        # Якщо файлу кешу немає – створюємо порожній
        if not os.path.exists(self.cache_path):
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({}, f)

        # Зчитування Spotify-креденшлів із config/options.ini
        config_parser = configparser.ConfigParser()
        config_parser.read("config/options.ini")
        client_id = config_parser.get("Credentials", "Spotify_ClientID", fallback=None)
        client_secret = config_parser.get("Credentials", "Spotify_ClientSecret", fallback=None)
        if not client_id or not client_secret:
            raise ValueError("Spotify Client_ID і Client_Secret мають бути вказані у config/options.ini")
        self.spotify = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret
            )
        )

    # -----------------------------------------------------
    # Допоміжні методи для роботи з кешем та чергою
    # -----------------------------------------------------
    def _load_cache(self) -> Dict[str, Any]:
        """Завантаження JSON‑кешу треків із файлу."""
        try:
            with open(self.track_cache_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Cache file not found or corrupted. Using empty cache.")
            return {}

    def _save_cache(self) -> None:
        """Збереження JSON‑кешу треків до файлу."""
        try:
            with open(self.track_cache_path, "w", encoding="utf-8") as file:
                json.dump(self.cache, file, indent=4)
        except Exception as e:
            logger.error(f"Error saving track cache: {e}")

    def _queue_file(self, guild_id: int) -> str:
        """Повертає шлях до файлу черги для заданого сервера."""
        return os.path.join(self.queue_path, f"{guild_id}_queue.json")

    def _load_queue(self, guild_id: int) -> List[Dict[str, Any]]:
        """
        Завантаження черги сервера з файлу.
        Якщо файл відсутній або пошкоджений – повертає пустий список.
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

    def _save_queue(self, guild_id: int, queue: List[Dict[str, Any]]) -> None:
        """Збереження черги (list) до JSON‑файлу для сервера."""
        path = self._queue_file(guild_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(queue, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving queue for guild {guild_id}: {e}")

    def ensure_queue(self, guild_id: int) -> List[Dict[str, Any]]:
        """
        Гарантовано повертає список черги для даного guild_id.
        Якщо в пам'яті черги немає – завантажує її з файлу.
        """
        if guild_id not in self.queues:
            self.queues[guild_id] = self._load_queue(guild_id)
        return self.queues[guild_id]

    async def _send_embed_footer(self, ctx: commands.Context, text: str) -> None:
        """Відправити повідомлення з Embed, де текст знаходиться у footer."""
        embed = discord.Embed()
        embed.set_footer(text=text)
        await ctx.send(embed=embed)

    async def ensure_voice(self, ctx: commands.Context) -> bool:
        """
        Перевірка, чи користувач підключений до голосового каналу.
        Якщо бот не підключений, приєднується до каналу користувача.
        """
        if not ctx.author.voice or not ctx.author.voice.channel:
            await self._send_embed_footer(ctx, "❌ Спочатку приєднайтесь до голосового каналу.")
            return False
        if not ctx.voice_client:
            await ctx.author.voice.channel.connect()
        elif ctx.voice_client.channel != ctx.author.voice.channel:
            await ctx.voice_client.move_to(ctx.author.voice.channel)
        return True

    # -----------------------------------------------------
    # Команда !play – додавання та відтворення треків
    # -----------------------------------------------------
    @commands.command(help="Додати трек або пошуковий запит у чергу та відтворити (YouTube / Spotify).")
    async def play(self, ctx: commands.Context, *, query: str) -> None:
        """
        Додає запит (або URL) у чергу.
        Якщо запит містить "spotify.com" – обробляється як Spotify,
        інакше – як YouTube-запит.
        """
        if not await self.ensure_voice(ctx):
            return

        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)

        try:
            if "spotify.com" in query:
                await self._handle_spotify(ctx, query, guild_id)
            else:
                await self._handle_youtube(ctx, query, guild_id)

            self._save_queue(guild_id, queue_)

            # Якщо нічого не грає – запускаємо відтворення
            if ctx.voice_client and not ctx.voice_client.is_playing():
                await self._play_next(ctx)
        except Exception as e:
            logger.error(f"Error in play command with query '{query}': {e}", exc_info=True)
            await self._send_embed_footer(ctx, f"❌ Помилка при обробці запиту: {e}")

    # -----------------------------------------------------
    # Команда jump – перехід до вказаного треку
    # -----------------------------------------------------
    @commands.command(help="Запускає трек з черги за заданим індексом (пропускає попередні).")
    async def jump(self, ctx: commands.Context, index: int) -> None:
        """
        Видаляє з черги всі треки до вказаного індексу
        та зупиняє поточний трек, щоб запустити наступний.
        """
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)

        if not queue_:
            await self._send_embed_footer(ctx, "❌ Черга порожня.")
            return

        if index < 1 or index > len(queue_):
            await self._send_embed_footer(ctx, f"❌ Невірний індекс треку. Введіть число від 1 до {len(queue_)}.")
            return

        skipped = index - 1
        # Видаляємо всі треки перед вказаним індексом
        for _ in range(skipped):
            queue_.pop(0)
        self._save_queue(guild_id, queue_)

        # Зупиняємо поточний трек (якщо грає), щоб викликати _play_next
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        else:
            await self._play_next(ctx)

        await self._send_embed_footer(ctx, f"⏭️ Перехід до треку: {queue_[0].get('title', 'Unknown')} (пропущено {skipped} трек(ів))")

    # -----------------------------------------------------
    # Обробка Spotify
    # -----------------------------------------------------
    async def _handle_spotify(self, ctx: commands.Context, query: str, guild_id: int) -> None:
        """Обробка запитів до Spotify та додавання треків у чергу."""
        logger.debug(f"_handle_spotify called with query: {query}")
        queue_ = self.ensure_queue(guild_id)

        try:
            if "playlist" in query:
                offset = 0
                all_items = []
                while True:
                    results = self.spotify.playlist_items(query, offset=offset, additional_types=["track"])
                    items = results.get("items", [])
                    if not items:
                        break
                    all_items.extend(items)
                    offset += len(items)
                    if not results.get("next"):
                        break

                total = len(all_items)
                added = 0
                for i in range(0, total, CHUNK_SIZE):
                    chunk = all_items[i : i + CHUNK_SIZE]
                    for item in chunk:
                        track_obj = item.get("track")
                        if track_obj:
                            processed = await self._process_spotify_track(track_obj)
                            if processed:
                                queue_.append(processed)
                                added += 1
                    await asyncio.sleep(CHUNK_DELAY)

                await self._send_embed_footer(ctx, f"🎵 Додано {added} трек(ів) зі Spotify-плейлиста.")
                self._save_queue(guild_id, queue_)
            else:
                # Обробка окремого треку
                track_obj = self.spotify.track(query)
                processed_track = await self._process_spotify_track(track_obj)
                if processed_track:
                    queue_.append(processed_track)
                    self._save_queue(guild_id, queue_)
                    await self._send_embed_footer(ctx, f"🎵 Додано трек зі Spotify: {processed_track.get('title', 'Unknown')}")
        except Exception as e:
            logger.error(f"Error processing Spotify link '{query}': {e}", exc_info=True)
            await self._send_embed_footer(ctx, f"❌ Spotify error: {e}")

    async def _process_spotify_track(self, track_obj: dict) -> Optional[Dict[str, Any]]:
        """
        Обробляє об'єкт треку з Spotify та повертає словник з даними треку.
        Якщо трек вже в кеші, повертає його з кешу.
        """
        try:
            title_search = f"{track_obj.get('name')} - {track_obj['artists'][0].get('name')}"
            if title_search in self.cache:
                logger.info(f"Трек вже в кеші: {title_search}")
                return self.cache[title_search]

            info = await asyncio.to_thread(self.ytdl.extract_info, f"ytsearch:{title_search}", False)
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
            logger.warning(f"Spotify-трек '{track_obj.get('name', 'Unknown')}' обробити не вдалося: {e}")
            return None

    # -----------------------------------------------------
    # Обробка YouTube
    # -----------------------------------------------------
    async def _handle_youtube(self, ctx: commands.Context, query: str, guild_id: int) -> None:
        """Обробка YouTube-запитів та додавання треків/плейлистів у чергу."""
        logger.debug(f"_handle_youtube called with query: {query}")
        queue_ = self.ensure_queue(guild_id)

        try:
            info = await asyncio.to_thread(self.ytdl.extract_info, query, download=False)

            if info and "entries" in info and info["entries"]:
                entries = info["entries"]
                total = len(entries)
                added_count = 0
                skipped_count = 0

                for i in range(0, total, CHUNK_SIZE):
                    chunk = entries[i : i + CHUNK_SIZE]
                    for e in chunk:
                        if e is None or e.get("availability") != "public":
                            skipped_count += 1
                            continue
                        track_url = e.get("webpage_url", e.get("url"))
                        track_data = {
                            "title": e.get("title", "Unknown"),
                            "url": track_url
                        }
                        if e.get("is_live"):
                            track_data["title"] += " [Live Stream]"
                        queue_.append(track_data)
                        added_count += 1

                    await asyncio.sleep(CHUNK_DELAY)

                await self._send_embed_footer(
                    ctx,
                    f"🎵 Додано {added_count} трек(ів) з YouTube-плейлиста. Пропущено: {skipped_count}."
                )
                self._save_queue(guild_id, queue_)
            else:
                if not info:
                    await self._send_embed_footer(ctx, "❌ Не вдалося знайти/відтворити цей запит.")
                    return
                if info.get("availability") != "public":
                    await self._send_embed_footer(ctx, "❌ Це відео недоступне або приватне.")
                    return

                title = info.get("title", "Unknown")
                url = info.get("webpage_url") or info.get("url", "")
                track_data = {"title": title, "url": url}
                queue_.append(track_data)
                self._save_queue(guild_id, queue_)
                await self._send_embed_footer(ctx, f"🎵 Додано трек: {title}")
        except Exception as e:
            logger.error(f"Помилка обробки YouTube-запиту '{query}': {e}", exc_info=True)
            await self._send_embed_footer(ctx, f"❌ YouTube error: {e}")

    # -----------------------------------------------------
    # Відтворення наступного треку
    # -----------------------------------------------------
    async def _play_next(self, ctx: commands.Context) -> None:
        """Відтворює наступний трек із черги."""
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)

        if not queue_:
            logger.debug(f"Черга порожня для guild_id={guild_id}.")
            await self._send_embed_footer(ctx, "✅ Черга порожня! Додайте більше треків для відтворення.")
            return

        track = queue_.pop(0)
        self._save_queue(guild_id, queue_)
        title = track.get("title", "Unknown")
        url = track.get("url", "")

        # Переконуємося, що бот підключений до голосового каналу
        if not ctx.voice_client:
            logger.debug("voice_client is None, не можемо програти трек.")
            if ctx.author.voice and ctx.author.voice.channel:
                await ctx.author.voice.channel.connect()
            else:
                await self._send_embed_footer(ctx, "❌ Немає активного голосового підключення.")
                return

        ffmpeg_options = "-vn"
        try:
            data = await asyncio.to_thread(self.ytdl.extract_info, url, download=False)
            if not data:
                await self._send_embed_footer(ctx, f"❌ Не вдалося обробити: {title}")
                await self._play_next(ctx)
                return

            stream_url = data.get("url")
            before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"

            source = discord.FFmpegPCMAudio(
                stream_url,
                executable=self.ffmpeg_path,
                before_options=before_options,
                options=ffmpeg_options
            )
            source = discord.PCMVolumeTransformer(source, volume=self.default_volume)
        except Exception as e:
            logger.error(f"Error creating FFmpegPCMAudio для треку {title}: {e}", exc_info=True)
            await self._send_embed_footer(ctx, f"❌ Не вдалося створити аудіо-джерело для: {title}")
            await self._play_next(ctx)
            return

        # Зберігаємо інформацію про поточний трек
        self.current_tracks[guild_id] = {"title": title, "duration": data.get("duration")}

        def after_playing(error: Optional[Exception]) -> None:
            if error:
                logger.error(f"Error after playing track '{title}': {error}", exc_info=True)
            else:
                logger.debug(f"Трек '{title}' відтворився без помилок.")
            self.current_tracks[guild_id] = None
            fut = asyncio.run_coroutine_threadsafe(self._play_next(ctx), self.bot.loop)
            try:
                fut.result()
            except Exception as e:
                logger.error(f"Error scheduling next track: {e}", exc_info=True)

        try:
            ctx.voice_client.play(source, after=after_playing)
            # Відправляємо повідомлення про поточний трек асинхронно
            asyncio.create_task(self._send_embed_footer(ctx, f"🎶 Зараз грає: {title}"))
        except Exception as e:
            logger.error(f"Error playing track '{title}': {e}", exc_info=True)
            await self._send_embed_footer(ctx, f"❌ Помилка відтворення: {title}. Переходимо до наступного...")
            await self._play_next(ctx)

    # -----------------------------------------------------
    # Інші команди для керування музикою
    # -----------------------------------------------------
    @commands.command(help="Зупинити музику і очистити чергу.")
    async def stop(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)

        if ctx.voice_client:
            queue_.clear()
            self._save_queue(guild_id, queue_)
            ctx.voice_client.stop()
            await self._send_embed_footer(ctx, "⏹️ Відтворення зупинено та черга очищена.")
        else:
            await self._send_embed_footer(ctx, "❌ Бот не в голосовому каналі!")

    @commands.command(help="Призупинити відтворення.")
    async def pause(self, ctx: commands.Context) -> None:
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await self._send_embed_footer(ctx, "⏸️ Відтворення призупинено.")
        else:
            await self._send_embed_footer(ctx, "❌ Немає що ставити на паузу!")

    @commands.command(help="Продовжити відтворення.")
    async def resume(self, ctx: commands.Context) -> None:
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await self._send_embed_footer(ctx, "▶️ Відтворення продовжено.")
        else:
            await self._send_embed_footer(ctx, "❌ Немає що відновлювати!")

    @commands.command(help="Пропустити поточний трек.")
    async def skip(self, ctx: commands.Context) -> None:
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await self._send_embed_footer(ctx, "⏭️ Трек пропущено.")
        else:
            await self._send_embed_footer(ctx, "❌ Немає що пропускати!")

    @commands.command(help="Показати перелік треків у черзі.")
    async def queue(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)

        if not queue_:
            await self._send_embed_footer(ctx, "❌ Черга порожня.")
            return

        view = QueueView(ctx, queue_, items_per_page=10)
        embed = view.get_embed()
        await ctx.send(embed=embed, view=view)

    @commands.command(help="Встановити гучність відтворення (0-100%).")
    async def volume(self, ctx: commands.Context, volume: int) -> None:
        if 0 <= volume <= 100:
            self.default_volume = volume / 100
            if ctx.voice_client and ctx.voice_client.source:
                if isinstance(ctx.voice_client.source, discord.PCMVolumeTransformer):
                    ctx.voice_client.source.volume = self.default_volume
            await self._send_embed_footer(ctx, f"🔊 Гучність встановлено на {volume}%.")
        else:
            await self._send_embed_footer(ctx, "❌ Вкажіть значення від 0 до 100.")

    @commands.command(help="Показати зараз відтворюваний трек.")
    async def nowplaying(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        current = self.current_tracks.get(guild_id)
        if current:
            title = current.get("title", "Unknown")
            duration = current.get("duration")
            if duration:
                minutes = int(duration // 60)
                seconds = int(duration % 60)
                duration_str = f"{minutes}:{seconds:02d}"
                await self._send_embed_footer(ctx, f"🎶 Зараз відтворюється: {title} [{duration_str}]")
            else:
                await self._send_embed_footer(ctx, f"🎶 Зараз відтворюється: {title}")
        else:
            await self._send_embed_footer(ctx, "❌ Наразі нічого не відтворюється.")

    @commands.command(help="Видалити трек з черги за індексом.")
    async def remove(self, ctx: commands.Context, index: int) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)

        if index < 1 or index > len(queue_):
            await self._send_embed_footer(ctx, "❌ Невірний індекс треку.")
        else:
            removed = queue_.pop(index - 1)
            self._save_queue(guild_id, queue_)
            await self._send_embed_footer(ctx, f"✅ Видалено трек: {removed.get('title', 'Unknown')}")

    @commands.command(help="Перемішати чергу.")
    async def shuffle(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)

        if not queue_:
            await self._send_embed_footer(ctx, "❌ Черга порожня.")
        else:
            random.shuffle(queue_)
            self._save_queue(guild_id, queue_)
            await self._send_embed_footer(ctx, "🔀 Черга перемішана.")

    @commands.command(help="Очистити всю чергу, не зупиняючи поточний трек.")
    async def clearqueue(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)

        if not queue_:
            await self._send_embed_footer(ctx, "❌ Черга і так порожня.")
        else:
            queue_.clear()
            self._save_queue(guild_id, queue_)
            await self._send_embed_footer(ctx, "🗑️ Вся черга успішно очищена!")

        
# -----------------------------------------------------
# Підключення Music Cog до бота
# -----------------------------------------------------
def setup(bot: commands.Bot) -> None:
    try:
        bot.add_cog(Music(bot))
        logger.info("Music Cog successfully loaded.")
    except Exception as e:
        logger.error(f"Failed to load Music Cog: {e}", exc_info=True)
        raise


