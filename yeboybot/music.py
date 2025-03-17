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

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("bot")

CHUNK_SIZE = 25
CHUNK_DELAY = 0.0
MAX_RETRY = 5  # максимальна кількість спроб отримати інформацію про трек

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
# Клас Music – основний модуль відтворення музики
###############################################
class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Завантаження конфігурації
        self.config = type("Config", (), {})()
        self.config.auto_playlist_dir = pathlib.Path("data/apl")
        self.config.enable_queue_history_global = True

        # Ініціалізація менеджера автосписку
        from yeboybot.autoplaylist import AutoPlaylistManager
        self.apl_manager = AutoPlaylistManager(self)

        self.queues: Dict[int, List[Dict[str, Any]]] = {}
        self.current_tracks: Dict[int, Optional[Dict[str, Any]]] = {}

        self.data_path = "data/music"
        self.queue_path = "data/queues"
        self.cache_path = os.path.join(self.data_path, "cache.json")
        self.track_cache_path = os.path.join(self.data_path, "track_cache.json")
        self.default_volume = 0.5

        # Створення необхідних директорій
        os.makedirs(self.data_path, exist_ok=True)
        os.makedirs(self.queue_path, exist_ok=True)

        self.cache = self._load_cache()

        # Шляхи до ffmpeg та ffprobe (можна винести у конфіг)
        self.ffmpeg_path = r"E:\Discord Bot\Bot\bin\ffmpeg.exe"
        self.ffprobe_path = r"E:\Discord Bot\Bot\bin\ffprobe.exe"
        if not os.path.isfile(self.ffmpeg_path):
            raise FileNotFoundError(f"FFmpeg не знайдено: {self.ffmpeg_path}")
        if not os.path.isfile(self.ffprobe_path):
            raise FileNotFoundError(f"FFprobe не знайдено: {self.ffprobe_path}")

        logger.debug(f"FFmpeg: {self.ffmpeg_path}")
        logger.debug(f"FFprobe: {self.ffprobe_path}")

        # Ініціалізація youtube_dl
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

        # Ініціалізація файлу кешу, якщо не існує
        if not os.path.exists(self.cache_path):
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({}, f)

        # Завантаження налаштувань Spotify
        config_parser = configparser.ConfigParser()
        config_parser.read("config/options.ini")
        client_id = config_parser.get("Credentials", "Spotify_ClientID", fallback=None)
        client_secret = config_parser.get("Credentials", "Spotify_ClientSecret", fallback=None)
        if not client_id or not client_secret:
            raise ValueError("Потрібно вказати Spotify_ClientID та Spotify_ClientSecret у config/options.ini")

        self.spotify = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        )

        # Асинхронний замок для запобігання одночасного виклику _play_next
        self.play_lock = asyncio.Lock()

    class DummyContext:
        """Контекст для автозапуску (без реального відправлення повідомлень)."""
        def __init__(self, guild: discord.Guild, voice_client: discord.VoiceClient, author: discord.Member):
            self.guild = guild
            self.voice_client = voice_client
            self.author = author

        async def send(self, *args, **kwargs):
            pass

    @staticmethod
    def preprocess_youtube_url(url: str) -> str:
        """Перетворює URL з music.youtube.com на стандартний www.youtube.com."""
        parsed = urllib.parse.urlparse(url)
        if "music.youtube.com" in parsed.netloc:
            parsed = parsed._replace(netloc="www.youtube.com")
            return parsed.geturl()
        return url

    ##########################################
    # Методи роботи з кешем та файлами
    ##########################################
    def _load_cache(self) -> Dict[str, Any]:
        try:
            with open(self.track_cache_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Файл кешу відсутній або пошкоджений. Використовується порожній кеш.")
            return {}

    def _save_cache(self) -> None:
        try:
            with open(self.track_cache_path, "w", encoding="utf-8") as file:
                json.dump(self.cache, file, indent=4)
        except Exception as e:
            logger.error(f"Помилка при збереженні кешу: {e}")

    def _queue_file(self, guild_id: int) -> str:
        return os.path.join(self.queue_path, f"{guild_id}_queue.json")

    def _load_queue(self, guild_id: int) -> List[Dict[str, Any]]:
        path = self._queue_file(guild_id)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"Файл черги {path} пошкоджено. Створюється порожня черга.")
                return []
        return []

    def _save_queue(self, guild_id: int, queue: List[Dict[str, Any]]) -> None:
        path = self._queue_file(guild_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(queue, f, indent=4)
        except Exception as e:
            logger.error(f"Помилка збереження черги для guild {guild_id}: {e}")

    def ensure_queue(self, guild_id: int) -> List[Dict[str, Any]]:
        if guild_id not in self.queues:
            self.queues[guild_id] = self._load_queue(guild_id)
        return self.queues[guild_id]

    async def _send_embed_footer(self, ctx: commands.Context, text: str) -> None:
        """Відправляє повідомлення у вигляді Embed з футером."""
        embed = discord.Embed()
        embed.set_footer(text=text)
        await ctx.send(embed=embed)

    async def ensure_voice(self, ctx: commands.Context) -> bool:
        """Перевіряє підключення до голосового каналу та підключає за необхідності."""
        if not ctx.author.voice or not ctx.author.voice.channel:
            await self._send_embed_footer(ctx, "❌ Спочатку підключіться до голосового каналу.")
            return False
        if not ctx.voice_client:
            await ctx.author.voice.channel.connect()
        elif ctx.voice_client.channel != ctx.author.voice.channel:
            await ctx.voice_client.move_to(ctx.author.voice.channel)
        return True

    async def autoplay(self, ctx: commands.Context, track: str) -> None:
        """Додає трек автозапуску до черги та запускає відтворення, якщо нічого не грає."""
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)
        queue_.append({"title": "Autoplay Track", "url": track})
        self._save_queue(guild_id, queue_)
        if ctx.voice_client and not ctx.voice_client.is_playing():
            await self._play_next(ctx)

    ################################################
    # Події та автозапуск при завантаженні
    ################################################
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        logger.info("Cog Music готовий до роботи.")
        await self.join_owner_and_autoplay()

    async def join_owner_and_autoplay(self) -> None:
        """Підключається до голосового каналу власника та запускає автозапуск."""
        config_parser = configparser.ConfigParser()
        config_parser.read("config/options.ini")
        owner_id_str = config_parser.get("Permissions", "OwnerID", fallback="auto")
        if owner_id_str.lower() == "auto":
            logger.debug("OwnerID=auto; автопідключення пропущено.")
            return
        try:
            owner_id = int(owner_id_str)
        except ValueError:
            logger.error("Невірне значення OwnerID.")
            return

        for guild in self.bot.guilds:
            member = guild.get_member(owner_id)
            if member and member.voice and member.voice.channel:
                voice_channel = member.voice.channel
                if not guild.voice_client:
                    try:
                        await voice_channel.connect()
                    except Exception as e:
                        logger.error(f"Не вдалося підключитись до голосового каналу: {e}")
                        continue

                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                autoplaylist_path = os.path.join(base_dir, "config", "_autoplaylist.txt")
                if not os.path.exists(autoplaylist_path):
                    logger.error(f"Файл автосписку відсутній: {autoplaylist_path}")
                    continue

                with open(autoplaylist_path, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
                if not lines:
                    logger.error("Файл автосписку порожній.")
                    continue

                track = random.choice(lines)
                dummy_ctx = self.DummyContext(guild, guild.voice_client, member)
                await self.autoplay(dummy_ctx, track)
                logger.info(f"Автовідтворення запущено з треком: {track}")
                break

    ################################################
    # Команди користувача
    ################################################
    @commands.command(help="Запустити авторизацію YouTube OAuth2.")
    async def youtube_auth(self, ctx: commands.Context) -> None:
        logger.info("Запит авторизації YouTube OAuth2.")
        try:
            from yeboybot.ytdlp_oauth2_plugin import YouTubeOAuth2Handler
            token_data = await YouTubeOAuth2Handler.initialize_oauth()
            if token_data:
                await ctx.send("✅ YouTube OAuth2 авторизація успішна!")
            else:
                await ctx.send("❌ Не вдалося отримати токен YouTube OAuth2.")
        except Exception as e:
            logger.exception("Помилка YouTube OAuth2:")
            await ctx.send(f"❌ Помилка: {e}")

    @commands.command(help="Додати трек або пошук (YouTube/Spotify) у чергу.")
    async def play(self, ctx: commands.Context, *, query: str) -> None:
        logger.info(f"Команда play викликана з запитом: {query}")
        if not await self.ensure_voice(ctx):
            return
        guild_id = ctx.guild.id

        # Якщо зараз грає автотрек – зупиняємо його
        current = self.current_tracks.get(guild_id)
        if current and current.get("title") == "Autoplay Track":
            if ctx.voice_client and ctx.voice_client.is_playing():
                ctx.voice_client.stop()

        queue_ = self.ensure_queue(guild_id)
        try:
            if "spotify.com" in query:
                await self._handle_spotify(ctx, query, guild_id)
            else:
                await self._handle_youtube(ctx, query, guild_id)
            self._save_queue(guild_id, queue_)

            # Видаляємо автотреки з черги, якщо користувач додає новий трек вручну
            new_queue = [track for track in queue_ if track.get("title") != "Autoplay Track"]
            if len(new_queue) != len(queue_):
                self.queues[guild_id] = new_queue
                self._save_queue(guild_id, new_queue)

            # Якщо нічого не грає – запускаємо наступний трек
            if ctx.voice_client and not ctx.voice_client.is_playing():
                await self._play_next(ctx)
        except Exception as e:
            logger.exception(f"Помилка в play: {e}")
            await self._send_embed_footer(ctx, f"❌ Помилка: {e}")

    @commands.command(help="Перейти до треку з індексом (пропустити попередні).")
    async def jump(self, ctx: commands.Context, index: int) -> None:
        logger.info(f"Команда jump викликана для індексу: {index}")
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)
        if not queue_:
            await self._send_embed_footer(ctx, "❌ Черга порожня.")
            return
        if index < 1 or index > len(queue_):
            await self._send_embed_footer(ctx, f"❌ Невірний індекс: 1..{len(queue_)}")
            return
        skipped = index - 1
        for _ in range(skipped):
            queue_.pop(0)
        self._save_queue(guild_id, queue_)
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        else:
            await self._play_next(ctx)
        await self._send_embed_footer(ctx, f"⏭️ Перейшли до треку: {queue_[0].get('title','Unknown')} (пропущено {skipped})")

    @commands.command(help="Поставити відтворення на паузу.")
    async def pause(self, ctx: commands.Context) -> None:
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await self._send_embed_footer(ctx, "⏸️ Паузу увімкнено.")
        else:
            await self._send_embed_footer(ctx, "❌ Нема чого ставити на паузу.")

    @commands.command(help="Відновити відтворення.")
    async def resume(self, ctx: commands.Context) -> None:
        if ctx.voice_client and ctx.voice_client.is_paузed():
            ctx.voice_client.resume()
            await self._send_embed_footer(ctx, "▶️ Відтворення відновлено.")
        else:
            await self._send_embed_footer(ctx, "❌ Нема чого відновлювати.")

    @commands.command(help="Пропустити поточний трек.")
    async def skip(self, ctx: commands.Context) -> None:
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await self._send_embed_footer(ctx, "⏭️ Пропущено поточний трек.")
        else:
            await self._send_embed_footer(ctx, "❌ Немає активного треку для пропуску.")

    @commands.command(help="Показати список треків у черзі.")
    async def queue(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)
        if not queue_:
            await self._send_embed_footer(ctx, "❌ Черга порожня.")
            return
        view = QueueView(ctx, queue_, items_per_page=10)
        embed = view.get_embed()
        await ctx.send(embed=embed, view=view)

    @commands.command(help="Змінити гучність (0-100%).")
    async def volume(self, ctx: commands.Context, volume: int) -> None:
        if 0 <= volume <= 100:
            self.default_volume = volume / 100
            if ctx.voice_client and ctx.voice_client.source:
                if isinstance(ctx.voice_client.source, discord.PCMVolumeTransformer):
                    ctx.voice_client.source.volume = self.default_volume
            await self._send_embed_footer(ctx, f"🔊 Гучність встановлено на: {volume}%.")
        else:
            await self._send_embed_footer(ctx, "❌ Невірне значення (0-100).")

    @commands.command(help="Показати інформацію про поточний трек.")
    async def nowplaying(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        current = self.current_tracks.get(guild_id)
        if current:
            t = current.get("title", "Unknown")
            dur = current.get("duration")
            if dur:
                m = dur // 60
                s = dur % 60
                await self._send_embed_footer(ctx, f"🎶 Зараз грає: {t} [{m}:{s:02d}]")
            else:
                await self._send_embed_footer(ctx, f"🎶 Зараз грає: {t}")
        else:
            await self._send_embed_footer(ctx, "❌ Наразі нічого не грає.")

    @commands.command(help="Видалити трек із черги за індексом.")
    async def remove(self, ctx: commands.Context, index: int) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)
        if index < 1 or index > len(queue_):
            await self._send_embed_footer(ctx, "❌ Невірний індекс для видалення.")
        else:
            removed = queue_.pop(index - 1)
            self._save_queue(guild_id, queue_)
            await self._send_embed_footer(ctx, f"✅ Видалено: {removed.get('title','Unknown')}")

    @commands.command(help="Перемішати чергу.")
    async def shuffle(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)
        if not queue_:
            await self._send_embed_footer(ctx, "❌ Черга порожня.")
        else:
            random.shuffle(queue_)
            self._save_queue(guild_id, queue_)
            await self._send_embed_footer(ctx, "🔀 Чергу перемішано.")

    @commands.command(help="Очистити чергу (без зупинки поточного треку).")
    async def clearqueue(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        queue_ = self.ensure_queue(guild_id)
        if queue_:
            queue_.clear()
            self._save_queue(guild_id, queue_)
            await self._send_embed_footer(ctx, "🗑️ Чергу очищено.")
        else:
            await self._send_embed_footer(ctx, "❌ Черга вже порожня.")

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
            await self._send_embed_footer(ctx, "❌ Бот не підключено до голосового каналу.")

    ################################################
    # Обробка треків (Spotify/YouTube)
    ################################################
    async def _handle_spotify(self, ctx: commands.Context, query: str, guild_id: int) -> None:
        logger.info(f"Обробка Spotify запиту: {query}")
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

                total = len(all_items)
                added = 0
                for i in range(0, total, CHUNK_SIZE):
                    chunk = all_items[i:i+CHUNK_SIZE]
                    for item in chunk:
                        track_obj = item.get("track")
                        if track_obj:
                            processed = await self._process_spotify_track(track_obj)
                            if processed:
                                queue_.append(processed)
                    added += len(chunk)
                    self._save_queue(guild_id, queue_)
                    await ctx.send(f"Додано {added}/{total} треків зі Spotify-плейлиста...")
                    if CHUNK_DELAY > 0:
                        await asyncio.sleep(CHUNK_DELAY)

                await self._send_embed_footer(ctx, f"✅ Усього додано {added} трек(ів) зі Spotify-плейлиста.")

            elif sp_type == "album":
                album_data = self.spotify.album(query)
                tracks = album_data.get("tracks", {}).get("items", [])
                if not tracks:
                    await self._send_embed_footer(ctx, "❌ Треки в альбомі не знайдено.")
                    return

                total = len(tracks)
                added = 0
                for i in range(0, total, CHUNK_SIZE):
                    chunk = tracks[i:i+CHUNK_SIZE]
                    for track in chunk:
                        processed = await self._process_spotify_track(track)
                        if processed:
                            queue_.append(processed)
                    added += len(chunk)
                    self._save_queue(guild_id, queue_)
                    await ctx.send(f"Додано {added}/{total} треків з альбому...")
                    if CHUNK_DELAY > 0:
                        await asyncio.sleep(CHUNK_DELAY)

                await self._send_embed_footer(ctx, f"✅ Усього додано {added} трек(ів) з альбому.")

            elif sp_type == "track":
                track_obj = self.spotify.track(query)
                processed = await self._process_spotify_track(track_obj)
                if processed:
                    queue_.append(processed)
                    self._save_queue(guild_id, queue_)
                    await self._send_embed_footer(ctx, f"🎵 Додано трек зі Spotify: {processed.get('title', 'Unknown')}")
            else:
                await self._send_embed_footer(ctx, "❌ Невідомий тип URL Spotify (має бути track, album або playlist).")

        except Exception as e:
            logger.exception(f"Spotify error для запиту {query}: {e}")
            await self._send_embed_footer(ctx, f"❌ Помилка Spotify: {e}")

    async def _process_spotify_track(self, track_obj: dict) -> Optional[Dict[str, Any]]:
        try:
            title = track_obj.get('name', 'Unknown')
            artist = track_obj.get('artists', [{}])[0].get('name', 'Unknown')
            title_search = f"{title} - {artist}"
            if title_search in self.cache:
                logger.debug(f"Трек знайдено в кеші: {title_search}")
                return self.cache[title_search]

            info = await asyncio.to_thread(self.ytdl.extract_info, f"ytsearch:{title_search}", download=False)
            if info and "entries" in info and info["entries"]:
                best = info["entries"][0]
                track_data = {
                    "title": best.get("title", "Unknown"),
                    "url": best.get("webpage_url", "")
                }
                self.cache[title_search] = track_data
                self._save_cache()
                return track_data
            else:
                logger.warning(f"Не знайдено на YouTube: {title_search}")
                return None
        except Exception as e:
            logger.warning(f"Помилка обробки Spotify-треку '{track_obj.get('name', 'Unknown')}': {e}")
            return None

    async def _handle_youtube(self, ctx: commands.Context, query: str, guild_id: int) -> None:
        query = self.preprocess_youtube_url(query)
        queue_ = self.ensure_queue(guild_id)
        logger.info(f"Обробка YouTube запиту: {query}")
        try:
            info = await asyncio.to_thread(self.ytdl.extract_info, query, download=False)
            if info and "entries" in info and info["entries"]:
                entries = info["entries"]
                total = len(entries)
                added = 0

                for i in range(0, total, CHUNK_SIZE):
                    chunk = entries[i:i+CHUNK_SIZE]
                    for entry in chunk:
                        if entry is None or entry.get("availability") != "public":
                            continue
                        track = {
                            "title": entry.get("title", "Unknown"),
                            "url": entry.get("webpage_url", entry.get("url", ""))
                        }
                        if entry.get("is_live"):
                            track["title"] += " [Live]"
                        queue_.append(track)
                    added += len(chunk)
                    self._save_queue(guild_id, queue_)
                    await ctx.send(f"Додано {added}/{total} треків із YouTube-плейлиста...")
                    if CHUNK_DELAY > 0:
                        await asyncio.sleep(CHUNK_DELAY)

                await self._send_embed_footer(ctx, f"✅ Усього додано {added} трек(ів) з YouTube-плейлиста.")
            else:
                # Обробка окремого відео
                if not info:
                    await self._send_embed_footer(ctx, "❌ Не вдалося знайти відео за запитом.")
                    return
                if info.get("availability") != "public":
                    await self._send_embed_footer(ctx, "❌ Відео недоступне або приватне.")
                    return
                track = {
                    "title": info.get("title", "Unknown"),
                    "url": info.get("webpage_url") or info.get("url", "")
                }
                queue_.append(track)
                self._save_queue(guild_id, queue_)
                await self._send_embed_footer(ctx, f"🎵 Додано трек: {track['title']}")

        except Exception as e:
            logger.exception(f"YouTube error для запиту {query}: {e}")
            await self._send_embed_footer(ctx, f"❌ Помилка YouTube: {e}")

    ################################################
    # Відтворення наступного треку
    ################################################
    async def _play_next(self, ctx: commands.Context) -> None:
        """
        Відтворює наступний трек із черги.
        Якщо черга порожня – завантажує трек автосписку.
        Перед викликом play перевіряється, чи вже не грає аудіо, щоб уникнути помилок.
        """
        async with self.play_lock:
            guild_id = ctx.guild.id
            retry_count = 0

            # Якщо щось вже грає – не запускаємо новий трек
            if ctx.voice_client and ctx.voice_client.is_playing():
                logger.debug("Відтворення вже триває, новий трек не запускається.")
                return

            while retry_count < MAX_RETRY:
                queue_ = self.ensure_queue(guild_id)

                if not queue_:
                    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    autoplaylist_path = os.path.join(base_dir, "config", "_autoplaylist.txt")
                    if os.path.exists(autoplaylist_path):
                        with open(autoplaylist_path, "r", encoding="utf-8") as f:
                            lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
                        if lines:
                            random_track = random.choice(lines)
                            queue_.append({"title": "Autoplay Track", "url": random_track})
                            self._save_queue(guild_id, queue_)
                            logger.debug(f"Автовідтворення запущено з треком: {random_track}")
                        else:
                            logger.error("Файл автосписку порожній.")
                            await self._send_embed_footer(ctx, "❌ Файл автосписку порожній.")
                            return
                    else:
                        logger.error("Файл автосписку не знайдено.")
                        await self._send_embed_footer(ctx, "❌ Файл автосписку не знайдено.")
                        return

                # Беремо перший трек з черги
                track = queue_.pop(0)
                self._save_queue(guild_id, queue_)
                title = track.get("title", "Unknown")
                url = track.get("url", "")

                # Перевірка голосового підключення
                if not ctx.voice_client or not ctx.voice_client.is_connected():
                    if ctx.author and ctx.author.voice and ctx.author.voice.channel:
                        await ctx.author.voice.channel.connect()
                    else:
                        await self._send_embed_footer(ctx, "❌ Немає підключеного голосового каналу.")
                        return

                # Спроба отримати інформацію про трек
                try:
                    data = await asyncio.to_thread(self.ytdl.extract_info, url, download=False)
                except Exception as e:
                    logger.exception(f"Помилка extract_info для {title}: {e}")
                    retry_count += 1
                    continue

                if not data:
                    logger.warning(f"Не вдалося отримати дані для {title}, пропуск треку.")
                    retry_count += 1
                    continue

                stream_url = data.get("url")
                if not stream_url:
                    logger.warning(f"Немає stream URL для {title}, пропуск треку.")
                    retry_count += 1
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
                    logger.exception(f"Помилка створення FFmpegPCMAudio для {title}: {e}")
                    retry_count += 1
                    continue

                self.current_tracks[guild_id] = {"title": title, "duration": data.get("duration")}

                def after_playing(error: Optional[Exception]) -> None:
                    if error:
                        logger.error(f"Помилка після програвання {title}: {error}", exc_info=True)
                    else:
                        logger.debug(f"Трек '{title}' завершив відтворення.")
                    self.current_tracks[guild_id] = None
                    # Запускаємо наступний трек у окремій задачі
                    asyncio.run_coroutine_threadsafe(self._play_next(ctx), self.bot.loop)

                try:
                    ctx.voice_client.play(source, after=after_playing)
                    logger.info(f"▶️ Почато відтворення: {title}")
                    return  # успішно запустили трек, виходимо з циклу
                except discord.errors.ClientException as ce:
                    logger.error(f"ClientException при відтворенні {title}: {ce}", exc_info=True)
                    await asyncio.sleep(0.3)
                    retry_count += 1
                except Exception as e:
                    logger.exception(f"Помилка запуску треку {title}: {e}")
                    await self._send_embed_footer(ctx, f"❌ Помилка відтворення: {title}. Наступний...")
                    retry_count += 1

            logger.error("Максимальна кількість спроб відтворення вичерпана.")
            await self._send_embed_footer(ctx, "❌ Не вдалося запустити наступний трек після декількох спроб.")

    async def setup(self, bot: commands.Bot) -> None:
        await bot.add_cog(self)


async def setup(bot: commands.Bot) -> None:
    try:
        await bot.add_cog(Music(bot))
        logger.info("Music Cog завантажено успішно.")
    except Exception as e:
        logger.exception(f"Не вдалося додати Music Cog: {e}")
        raise
