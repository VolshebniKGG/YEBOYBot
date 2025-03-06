import os
import pathlib
import json
import asyncio
import logging
import configparser
import random
import urllib.parse
from typing import Any, Dict, List, Optional

import discord
from discord.ext import commands

import yt_dlp as youtube_dl
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from yeboybot.autoplaylist import AutoPlaylistManager
from yeboybot.ytdlp_oauth2_plugin import YouTubeOAuth2Handler
from yeboybot.exceptions import ExtractionError, SpotifyError, MusicbotException

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("bot")

CHUNK_SIZE = 25
CHUNK_DELAY = 0.1


###############################################
# Клас QueueView – для пагінації черги треків #
###############################################
class QueueView(discord.ui.View):
    """View для перегляду та пагінації черги треків у Discord."""
    def __init__(self, ctx: commands.Context, queue: List[Dict[str, Any]], items_per_page: int = 10):
        super().__init__(timeout=60)
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
                f"{start + i + 1}. {track.get('title', 'Unknown')}" for i, track in enumerate(page_items)
            )
            embed.description = description
        else:
            embed.description = "Черга порожня."
        embed.set_footer(text=f"Сторінка {self.current_page + 1}/{self.get_page_count()}")
        return embed

    @discord.ui.button(label="⏪", style=discord.ButtonStyle.primary, custom_id="first_page")
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary, custom_id="previous_page")
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary, custom_id="next_page")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.get_page_count() - 1:
            self.current_page += 1
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="⏩", style=discord.ButtonStyle.primary, custom_id="last_page")
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = self.get_page_count() - 1
        await interaction.response.edit_message(embed=self.get_embed(), view=self)


###############################################
# Клас Music – основний модуль відтворення музики #
###############################################
class Music(commands.Cog):
    """
    Cog для відтворення музики з YouTube та Spotify із розширеним функціоналом.
    Також містить функціонал автозапуску – при готовності бота він перевіряє, чи власник підключений
    до голосового каналу, і якщо так, автоматично приєднується та починає відтворення випадкового треку
    із _autoplaylist.txt.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Завантаження конфігурації автосписків
        self.config = type("Config", (), {})()
        self.config.auto_playlist_dir = pathlib.Path("data/apl")
        self.config.enable_queue_history_global = True

        self.apl_manager = AutoPlaylistManager(self)

        self.queues: Dict[int, List[Dict[str, Any]]] = {}
        self.current_tracks: Dict[int, Optional[Dict[str, Any]]] = {}

        self.data_path = "data/music"
        self.queue_path = "data/queues"
        self.cache_path = os.path.join(self.data_path, "cache.json")
        self.track_cache_path = os.path.join(self.data_path, "track_cache.json")
        self.unavailable_log_path = os.path.join(self.data_path, "unavailable_log.json")
        self.processed_tracks: List[Any] = []
        self.default_volume = 0.5

        os.makedirs(self.data_path, exist_ok=True)
        os.makedirs(self.queue_path, exist_ok=True)

        self.cache = self._load_cache()

        self.ffmpeg_path = r"E:\Discord Bot\Bot\bin\ffmpeg.exe"
        self.ffprobe_path = r"E:\Discord Bot\Bot\bin\ffprobe.exe"
        if not os.path.isfile(self.ffmpeg_path):
            raise FileNotFoundError(f"FFmpeg не знайдено за адресою: {self.ffmpeg_path}")
        if not os.path.isfile(self.ffprobe_path):
            raise FileNotFoundError(f"FFprobe не знайдено за адресою: {self.ffprobe_path}")
        logger.info(f"FFmpeg знайдено: {self.ffmpeg_path}")
        logger.info(f"FFprobe знайдено: {self.ffprobe_path}")

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

        if not os.path.exists(self.cache_path):
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({}, f)

        # Налаштування Spotify
        config_parser = configparser.ConfigParser()
        config_parser.read("config/options.ini")
        client_id = config_parser.get("Credentials", "Spotify_ClientID", fallback=None)
        client_secret = config_parser.get("Credentials", "Spotify_ClientSecret", fallback=None)
        if not client_id or not client_secret:
            raise ValueError("Spotify Client_ID і Client_Secret мають бути вказані у config/options.ini")
        self.spotify = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        )

    # DummyContext для симуляції об'єкта Context без надсилання повідомлень
    class DummyContext:
        def __init__(self, guild, voice_client, author):
            self.guild = guild
            self.voice_client = voice_client
            self.author = author
        async def send(self, *args, **kwargs):
            pass

    @staticmethod
    def preprocess_youtube_url(url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        if "music.youtube.com" in parsed.netloc:
            parsed = parsed._replace(netloc="www.youtube.com")
            return parsed.geturl()
        return url

    ##########################################
    # Методи роботи з кешем та файлами черги #
    ##########################################
    def _load_cache(self) -> Dict[str, Any]:
        try:
            with open(self.track_cache_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Cache file not found or corrupted. Using empty cache.")
            return {}

    def _save_cache(self) -> None:
        try:
            with open(self.track_cache_path, "w", encoding="utf-8") as file:
                json.dump(self.cache, file, indent=4)
        except Exception as e:
            logger.error(f"Error saving track cache: {e}")

    def _queue_file(self, guild_id: int) -> str:
        return os.path.join(self.queue_path, f"{guild_id}_queue.json")

    def _load_queue(self, guild_id: int) -> List[Dict[str, Any]]:
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
        path = self._queue_file(guild_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(queue, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving queue for guild {guild_id}: {e}")

    def ensure_queue(self, guild_id: int) -> List[Dict[str, Any]]:
        if guild_id not in self.queues:
            self.queues[guild_id] = self._load_queue(guild_id)
        return self.queues[guild_id]

    async def _send_embed_footer(self, ctx: commands.Context, text: str) -> None:
        embed = discord.Embed()
        embed.set_footer(text=text)
        await ctx.send(embed=embed)

    async def ensure_voice(self, ctx: commands.Context) -> bool:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await self._send_embed_footer(ctx, "❌ Спочатку приєднайтесь до голосового каналу.")
            return False
        if not ctx.voice_client:
            await ctx.author.voice.channel.connect()
        elif ctx.voice_client.channel != ctx.author.voice.channel:
            await ctx.voice_client.move_to(ctx.author.voice.channel)
        return True

    async def autoplay(self, ctx: commands.Context, track: str) -> None:
        """
        Автоматично відтворює трек (URL) без надсилання повідомлень у текстовий канал.
        """
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)
        queue_.append({"title": "Autoplay Track", "url": track})
        self._save_queue(guild_id, queue_)
        if ctx.voice_client and not ctx.voice_client.is_playing():
            await self._play_next(ctx)

    ##########################################
    # Події та автозапуск
    ##########################################
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Виконуємо автозапуск лише при першому готовності бота
        await self.join_owner_and_autoplay()

    async def join_owner_and_autoplay(self) -> None:
        """
        Шукає власника (за ID з config/options.ini) серед серверів, де бот присутній,
        і якщо власник підключений до голосового каналу, приєднується до нього та запускає автозапуск треку.
        """
        config_parser = configparser.ConfigParser()
        config_parser.read("config/options.ini")
        owner_id_str = config_parser.get("Permissions", "OwnerID", fallback="auto")
        if owner_id_str.lower() == "auto":
            logger.info("OwnerID встановлено в 'auto'; автопідключення не виконуватиметься.")
            return
        try:
            owner_id = int(owner_id_str)
        except ValueError:
            logger.error("Невірне значення OwnerID у файлі конфігурації.")
            return

        for guild in self.bot.guilds:
            member = guild.get_member(owner_id)
            if member and member.voice and member.voice.channel:
                voice_channel = member.voice.channel
                if not guild.voice_client:
                    try:
                        await voice_channel.connect()
                        logger.info(f"Приєднано до голосового каналу {voice_channel} на сервері {guild.name} для власника {member}.")
                    except Exception as e:
                        logger.error(f"Не вдалося приєднатись до голосового каналу: {e}")
                        continue

                # Обчислюємо базовий каталог бота
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                autoplaylist_path = os.path.join(base_dir, "config", "_autoplaylist.txt")
                if not os.path.exists(autoplaylist_path):
                    logger.error(f"Файл автосписку не знайдено: {autoplaylist_path}")
                    continue

                with open(autoplaylist_path, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
                if not lines:
                    logger.error("Файл автосписку порожній.")
                    continue

                # Вибираємо випадковий трек із автосписку
                track = random.choice(lines)
                dummy_ctx = self.DummyContext(guild, guild.voice_client, member)
                await self.autoplay(dummy_ctx, track)
                logger.info(f"Запущено автозапуск з треком: {track}")
                break

    ##########################################
    # Команди для Discord бота
    ##########################################
    @commands.command(name="youtube_auth", help="Запустити процес авторизації YouTube OAuth2.")
    async def youtube_auth(self, ctx: commands.Context) -> None:
        try:
            token_data = await YouTubeOAuth2Handler.initialize_oauth()
            if token_data:
                await ctx.send("✅ YouTube OAuth2 авторизація пройшла успішно!")
            else:
                await ctx.send("❌ Не вдалося отримати токен YouTube OAuth2.")
        except Exception as e:
            logger.exception("YouTube OAuth2 error")
            await ctx.send(f"❌ Сталася помилка авторизації: {e}")

    @commands.command(help="Додати трек або пошуковий запит у чергу та відтворити (YouTube/Spotify).")
    async def play(self, ctx: commands.Context, *, query: str) -> None:
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
            if ctx.voice_client and not ctx.voice_client.is_playing():
                await self._play_next(ctx)
        except MusicbotException as mbe:
            logger.error(f"MusicbotException in play command: {mbe}", exc_info=True)
            await self._send_embed_footer(ctx, f"❌ Помилка: {mbe.message}")
        except Exception as e:
            logger.exception(f"Error in play command with query '{query}'")
            await self._send_embed_footer(ctx, f"❌ Помилка при обробці запиту: {e}")

    @commands.command(help="Запускає трек з черги за заданим індексом (пропускає попередні).")
    async def jump(self, ctx: commands.Context, index: int) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)
        if not queue_:
            await self._send_embed_footer(ctx, "❌ Черга порожня.")
            return
        if index < 1 or index > len(queue_):
            await self._send_embed_footer(ctx, f"❌ Невірний індекс треку. Введіть число від 1 до {len(queue_)}.")
            return
        skipped = index - 1
        for _ in range(skipped):
            queue_.pop(0)
        self._save_queue(guild_id, queue_)
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        else:
            await self._play_next(ctx)
        await self._send_embed_footer(ctx, f"⏭️ Перехід до треку: {queue_[0].get('title', 'Unknown')} (пропущено {skipped} трек(ів))")

    @commands.command(help="Призупинити відтворення.")
    async def pause(self, ctx: commands.Context) -> None:
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await self._send_embed_footer(ctx, "⏸️ Відтворення призупинено.")
        else:
            await self._send_embed_footer(ctx, "❌ Немає що призупиняти!")

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
            await self._send_embed_footer(ctx, "❌ Значення має бути від 0 до 100.")

    @commands.command(help="Показати інформацію про поточний трек.")
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
                await self._send_embed_footer(ctx, f"🎶 Зараз грає: {title} [{duration_str}]")
            else:
                await self._send_embed_footer(ctx, f"🎶 Зараз грає: {title}")
        else:
            await self._send_embed_footer(ctx, "❌ Наразі нічого не грає.")

    @commands.command(help="Видалити трек з черги за індексом.")
    async def remove(self, ctx: commands.Context, index: int) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)
        if index < 1 or index > len(queue_):
            await self._send_embed_footer(ctx, "❌ Невірний індекс треку.")
        else:
            removed = queue_.pop(index - 1)
            self._save_queue(guild_id, queue_)
            await self._send_embed_footer(ctx, f"✅ Видалено: {removed.get('title', 'Unknown')}")

    @commands.command(help="Перемішати чергу треків.")
    async def shuffle(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)
        if not queue_:
            await self._send_embed_footer(ctx, "❌ Черга порожня.")
        else:
            random.shuffle(queue_)
            self._save_queue(guild_id, queue_)
            await self._send_embed_footer(ctx, "🔀 Черга перемішана.")

    @commands.command(help="Очистити всю чергу (без зупинки поточного треку).")
    async def clearqueue(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)
        if not queue_:
            await self._send_embed_footer(ctx, "❌ Черга вже порожня.")
        else:
            queue_.clear()
            self._save_queue(guild_id, queue_)
            await self._send_embed_footer(ctx, "🗑️ Чергу очищено!")

    @commands.command(help="Зупинити відтворення та очистити чергу.")
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

    ##########################################
    # Методи для обробки треків (Spotify/YouTube)
    ##########################################
    async def _handle_spotify(self, ctx: commands.Context, query: str, guild_id: int) -> None:
        logger.debug(f"_handle_spotify: {query}")
        queue_ = self.ensure_queue(guild_id)
        parsed = urllib.parse.urlparse(query)
        path_parts = parsed.path.split('/')
        sp_type = path_parts[1] if len(path_parts) > 1 else ""
        try:
            if sp_type == "playlist":
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
                added = 0
                for i in range(0, len(all_items), CHUNK_SIZE):
                    chunk = all_items[i:i+CHUNK_SIZE]
                    for item in chunk:
                        track_obj = item.get("track")
                        if track_obj:
                            processed = await self._process_spotify_track(track_obj)
                            if processed:
                                queue_.append(processed)
                                added += 1
                    await asyncio.sleep(CHUNK_DELAY)
                await self._send_embed_footer(ctx, f"🎵 Додано {added} трек(ів) зі Spotify-плейлиста.")
            elif sp_type == "album":
                album_data = self.spotify.album(query)
                tracks = album_data.get("tracks", {}).get("items", [])
                if not tracks:
                    await self._send_embed_footer(ctx, "❌ Не вдалося знайти треки в цьому альбомі.")
                    return
                added = 0
                for track in tracks:
                    processed = await self._process_spotify_track(track)
                    if processed:
                        queue_.append(processed)
                        added += 1
                    await asyncio.sleep(CHUNK_DELAY)
                await self._send_embed_footer(ctx, f"🎵 Додано {added} трек(ів) з Spotify-альбому.")
            elif sp_type == "track":
                track_obj = self.spotify.track(query)
                processed = await self._process_spotify_track(track_obj)
                if processed:
                    queue_.append(processed)
                    await self._send_embed_footer(ctx, f"🎵 Додано трек зі Spotify: {processed.get('title', 'Unknown')}")
            else:
                await self._send_embed_footer(ctx, "❌ Невідомий тип Spotify URL. Підтримуються лише track, album та playlist.")
        except Exception as e:
            logger.exception(f"Spotify error for query '{query}': {e}")
            await self._send_embed_footer(ctx, f"❌ Spotify error: {e}")
        self._save_queue(guild_id, queue_)

    async def _process_spotify_track(self, track_obj: dict) -> Optional[Dict[str, Any]]:
        try:
            title_search = f"{track_obj.get('name')} - {track_obj['artists'][0].get('name')}"
            if title_search in self.cache:
                logger.info(f"Трек у кеші: {title_search}")
                return self.cache[title_search]
            info = await asyncio.to_thread(self.ytdl.extract_info, f"ytsearch:{title_search}", False)
            if info and "entries" in info and info["entries"]:
                best = info["entries"][0]
                track_data = {"title": best.get("title", "Unknown"), "url": best.get("webpage_url", "")}
                self.cache[title_search] = track_data
                self._save_cache()
                return track_data
            else:
                logger.warning(f"Не знайдено результату для: {title_search}")
                return None
        except Exception as e:
            logger.warning(f"Помилка обробки Spotify треку '{track_obj.get('name', 'Unknown')}': {e}")
            return None

    async def _handle_youtube(self, ctx: commands.Context, query: str, guild_id: int) -> None:
        query = self.preprocess_youtube_url(query)
        logger.debug(f"_handle_youtube: {query}")
        queue_ = self.ensure_queue(guild_id)
        try:
            info = await asyncio.to_thread(self.ytdl.extract_info, query, download=False)
            if info and "entries" in info and info["entries"]:
                entries = info["entries"]
                added = 0
                skipped = 0
                for i in range(0, len(entries), CHUNK_SIZE):
                    chunk = entries[i:i+CHUNK_SIZE]
                    for entry in chunk:
                        if entry is None or entry.get("availability") != "public":
                            skipped += 1
                            continue
                        track = {"title": entry.get("title", "Unknown"), "url": entry.get("webpage_url", entry.get("url", ""))}
                        if entry.get("is_live"):
                            track["title"] += " [Live Stream]"
                        queue_.append(track)
                        added += 1
                    await asyncio.sleep(CHUNK_DELAY)
                await self._send_embed_footer(ctx, f"🎵 Додано {added} трек(ів) з YouTube-плейлиста. Пропущено: {skipped}.")
            else:
                if not info:
                    await self._send_embed_footer(ctx, "❌ Не вдалося знайти запит.")
                    return
                if info.get("availability") != "public":
                    await self._send_embed_footer(ctx, "❌ Це відео недоступне або приватне.")
                    return
                track = {"title": info.get("title", "Unknown"), "url": info.get("webpage_url") or info.get("url", "")}
                queue_.append(track)
                await self._send_embed_footer(ctx, f"🎵 Додано трек: {track['title']}")
        except Exception as e:
            logger.exception(f"YouTube error for query '{query}': {e}")
            await self._send_embed_footer(ctx, f"❌ YouTube error: {e}")
        self._save_queue(guild_id, queue_)

    async def _play_next(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)
        while queue_:
            track = queue_.pop(0)
            self._save_queue(guild_id, queue_)
            title = track.get("title", "Unknown")
            url = track.get("url", "")

            if not ctx.voice_client:
                if ctx.author.voice and ctx.author.voice.channel:
                    await ctx.author.voice.channel.connect()
                else:
                    await self._send_embed_footer(ctx, "❌ Немає активного голосового підключення.")
                    return

            try:
                data = await asyncio.to_thread(self.ytdl.extract_info, url, download=False)
            except Exception as e:
                logger.exception(f"Error extracting info for {title}: {e}")
                continue

            if not data:
                logger.warning(f"Не вдалося отримати дані для {title}, пропускаємо...")
                continue

            stream_url = data.get("url")
            if not stream_url:
                logger.warning(f"Немає URL потоку для {title}, пропускаємо...")
                continue

            ffmpeg_opts = "-vn"
            before_opts = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
            try:
                source = discord.FFmpegPCMAudio(
                    stream_url,
                    executable=self.ffmpeg_path,
                    before_options=before_opts,
                    options=ffmpeg_opts
                )
                source = discord.PCMVolumeTransformer(source, volume=self.default_volume)
            except Exception as e:
                logger.exception(f"Error creating FFmpegPCMAudio for {title}: {e}")
                continue

            self.current_tracks[guild_id] = {"title": title, "duration": data.get("duration")}

            def after_playing(error: Optional[Exception]) -> None:
                if error:
                    logger.error(f"Error after playing {title}: {error}", exc_info=True)
                else:
                    logger.info(f"Трек '{title}' успішно програвся.")
                self.current_tracks[guild_id] = None
                # Викликаємо _play_next знову після завершення треку
                fut = asyncio.run_coroutine_threadsafe(self._play_next(ctx), self.bot.loop)
                try:
                    fut.result()
                except Exception as e:
                    logger.error(f"Error scheduling next track: {e}", exc_info=True)

            try:
                if ctx.voice_client.is_playing():
                    ctx.voice_client.stop()
                    await asyncio.sleep(0.5)
                ctx.voice_client.play(source, after=after_playing)
            except discord.errors.ClientException as ce:
                logger.error(f"ClientException while playing {title}: {ce}", exc_info=True)
                ctx.voice_client.stop()
                await asyncio.sleep(0.5)
                continue
            except Exception as e:
                logger.exception(f"Error playing {title}: {e}")
                await self._send_embed_footer(ctx, f"❌ Помилка відтворення: {title}. Переходимо до наступного...")
                continue
            # Якщо трек успішно запустився, виходимо з циклу
            break


    ##########################################
    # Підключення Cog до бота
    ##########################################
    async def setup(self, bot: commands.Bot) -> None:
        await bot.add_cog(self)


async def setup(bot: commands.Bot) -> None:
    try:
        await bot.add_cog(Music(bot))
        logger.info("Music Cog successfully loaded.")
    except Exception as e:
        logger.exception(f"Failed to load Music Cog: {e}")
        raise
