"""Opt-in Discord voice adapter; queue/source policy lives in music_engine.

No gateway connection or FFmpeg process is created when this module is imported.
"""
import asyncio
from dataclasses import dataclass
import logging
import subprocess
import threading
from urllib.parse import urlsplit

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .config import ROOT
from .media_environment import ffmpeg_path
from .music_engine import (MusicError, MusicManager, YTDLPResolver, spotify_track_id,
                           validate_source, failure_message, normalize_failure_code)
from .music_options import load_autoplaylist, load_music_options
from .security import UserError, guild_context
from .spotify import SpotifyMetadata

log = logging.getLogger(__name__)


class _OwnedVoiceClient(discord.VoiceClient):
    """The SDK handles timeout cleanup; own cancellation cleanup as well."""

    async def connect(self, **kwargs):
        try:
            return await super().connect(**kwargs)
        except asyncio.CancelledError:
            try:
                async with asyncio.timeout(10):
                    await self.disconnect(force=True)
            except Exception as error:
                log.warning("Cancelled music connect cleanup failed type=%s", type(error).__name__)
            finally:
                self.cleanup()
            raise


def _stream_url(value):
    """Only the resolver's supported HTTPS audio CDNs reach FFmpeg."""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        valid = (parsed.scheme == "https" and not parsed.username and not parsed.password
                 and parsed.port in {None, 443} and not parsed.fragment
                 and any(host.endswith("." + domain) or host == domain
                         for domain in ("googlevideo.com", "sndcdn.com")))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise MusicError("Непідтримуваний або небезпечний аудіопотік.")
    return value


class _OwnedAudioSource(discord.AudioSource):
    """AudioPlayer and an interrupted stream may request cleanup concurrently."""

    def __init__(self, source, loop, cleaned):
        self.source, self.loop, self._on_cleaned = source, loop, cleaned
        self._lock = threading.Lock()
        self._cleanup_claimed = False
        self.cleaned = asyncio.Event()
        self.finished = False
        self.after = None
        self.error = None
        self.delivered = False

    def read(self):
        return self.source.read()

    def is_opus(self):
        return self.source.is_opus()

    def dispatch(self, callback, *args):
        try:
            self.loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            # A late AudioPlayer callback can arrive after the event loop closes.
            pass

    def cleanup(self):
        with self._lock:
            if self._cleanup_claimed:
                return
            self._cleanup_claimed = True
        try:
            self.source.cleanup()
        except Exception as error:
            log.warning("FFmpeg cleanup failed type=%s", type(error).__name__)
        finally:
            self.dispatch(self._on_cleaned, self)


class FFmpegVoiceBackend:
    """Own one source and at most one asynchronous cleanup operation."""

    def __init__(self, voice, executable, volume):
        self.voice = voice
        self.executable = executable
        self.volume = volume
        self._source = None
        self._paused = False
        self._cleanup_task = None

    def _source_cleaned(self, source):
        source.cleaned.set()
        self._finalize(source)

    def _audio_finished(self, source, error):
        if not source.finished:
            source.finished = True
            source.error = error
        self._finalize(source)

    def _finalize(self, source):
        if not source.finished or not source.cleaned.is_set():
            return
        if self._source is source:
            self._source = None
        if not source.delivered:
            source.delivered = True
            if source.after is not None:
                source.after(source.error)

    def _schedule_cleanup(self, source):
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return

        async def cleanup():
            try:
                # FFmpeg's communicate()/wait() must never hold the gateway loop.
                await asyncio.to_thread(source.cleanup)
                await source.cleaned.wait()
            finally:
                self._cleanup_task = None
                self._finalize(source)

        self._cleanup_task = asyncio.create_task(cleanup(), name="music-source-cleanup")

    async def wait_stopped(self, timeout=10):
        task = self._cleanup_task
        if task is not None:
            try:
                async with asyncio.timeout(timeout):
                    await asyncio.shield(task)
            except TimeoutError:
                return False
        return self._source is None

    def is_connected(self):
        return self.voice.is_connected()

    def play(self, track, after):
        if not self.is_connected():
            raise MusicError("З'єднання з голосовим каналом втрачено.")
        if self._source is not None or self._cleanup_task is not None:
            raise MusicError("Попередній аудіопотік ще завершується.")
        if self.voice.is_playing() or self.voice.is_paused():
            raise MusicError("Попередній аудіопотік ще завершується.")
        stream = _stream_url(track.stream_url)
        loop = asyncio.get_running_loop()
        source = None
        try:
            raw = discord.FFmpegPCMAudio(
                stream, executable=self.executable, stderr=subprocess.DEVNULL,
                before_options=("-nostdin -protocol_whitelist https,tls,tcp,crypto "
                                "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
                                "-rw_timeout 15000000"),
                options="-vn -sn -dn -loglevel error")
            try:
                wrapped = discord.PCMVolumeTransformer(raw, volume=self.volume)
            except Exception:
                source = _OwnedAudioSource(raw, loop, self._source_cleaned)
                self._source = source
                raise
            source = _OwnedAudioSource(wrapped, loop, self._source_cleaned)
            source.after = after
            self._source = source

            def finished(error):
                source.dispatch(self._audio_finished, source, error)

            self.voice.play(source, after=finished)
            if self._paused:
                self.voice.pause()
        except Exception:
            if source is not None:
                source.after = None
                source.finished = True
                self.voice.stop()
                self._schedule_cleanup(source)
            raise MusicError("Не вдалося почати відтворення. Перевірте FFmpeg і voice-бібліотеки.") from None

    def stop(self):
        self.voice.stop()
        source = self._source
        if source is not None:
            source.finished = True
            self._schedule_cleanup(source)

    def set_paused(self, paused):
        self._paused = paused
        if paused and self.voice.is_playing():
            self.voice.pause()
        elif not paused and self.voice.is_paused():
            self.voice.resume()

    async def close(self):
        self.stop()
        await self.wait_stopped()
        try:
            async with asyncio.timeout(10):
                await self.voice.disconnect(force=True)
        except Exception as error:
            log.warning("Voice disconnect failed type=%s", type(error).__name__)


@dataclass
class Session:
    backend: FFmpegVoiceBackend
    player: object


class Music(commands.GroupCog, group_name="music", group_description="Музика та автоматична черга сервера"):
    def __init__(self, bot, *, options=None, manager=None, root=ROOT):
        self.bot = bot
        self.options = options if options is not None else load_music_options(root)
        self.executable = bot.settings.ffmpeg
        if manager is None:
            self.executable = ffmpeg_path(self.executable, root)
            manager = MusicManager(self.options, load_autoplaylist(root),
                                   YTDLPResolver(spotify=SpotifyMetadata.from_root(root)))
        self.manager = manager
        self.sessions = {}
        self._connections = asyncio.Lock()
        self._stopped_guilds = set()
        self._closing = False
        self._teardowns = {}

    async def cog_load(self):
        if not self._closing and self.bot.is_ready():
            self.autojoin.start()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._closing and not self.autojoin.is_running():
            self.autojoin.start()

    async def cog_unload(self):
        self._closing = True
        self.autojoin.cancel()
        task = self.autojoin.get_task()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*(self._remove(guild_id) for guild_id in tuple(self.sessions)),
                             return_exceptions=True)
        await self.manager.close()

    async def _reply(self, interaction, message):
        await interaction.followup.send(message, ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())

    async def _context(self, interaction, *, manager=False):
        guild = guild_context(interaction)
        await interaction.response.defer(ephemeral=True)
        if self.bot.user is None:
            raise UserError("Discord-клієнт ще не готовий.")
        try:
            async with asyncio.timeout(10):
                actor, bot_member = await asyncio.gather(
                    guild.fetch_member(interaction.user.id), guild.fetch_member(self.bot.user.id))
        except (discord.HTTPException, TimeoutError):
            raise UserError("Не вдалося перевірити доступ до голосового каналу. Повторіть пізніше.") from None
        if (actor.id != interaction.user.id or bot_member.id != self.bot.user.id
                or actor.guild.id != guild.id or bot_member.guild.id != guild.id or actor.bot):
            raise UserError("Не вдалося підтвердити учасника цього сервера.")
        if manager and not actor.guild_permissions.manage_guild:
            raise UserError("Для цієї дії потрібен дозвіл «Керувати сервером».")
        voice = actor.voice
        channel = voice.channel if voice else None
        if (not isinstance(channel, discord.VoiceChannel)
                or channel.guild.id != guild.id
                or (guild.afk_channel and channel.id == guild.afk_channel.id)):
            raise UserError("Спочатку приєднайтеся до звичайного голосового каналу цього сервера.")
        self._permissions(channel, actor, bot_member)
        session = self.sessions.get(guild.id)
        if guild.id in self._teardowns:
            raise UserError("Музичний плеєр ще завершує роботу. Зачекайте й повторіть.")
        if session and session.backend.voice.channel.id != channel.id:
            raise UserError("Приєднайтеся до того самого голосового каналу, де грає бот.")
        return guild, channel, actor, bot_member

    @staticmethod
    def _permissions(channel, actor, bot_member):
        for member in (actor, bot_member):
            permissions = channel.permissions_for(member)
            if not permissions.view_channel or not permissions.connect or not permissions.speak:
                raise UserError("Вам і боту потрібні «Переглядати канал», «Підключатися» та «Говорити».")

    async def _ensure(self, guild, channel, *, automatic=False):
        try:
            async with asyncio.timeout(30):
                async with self._connections:
                    if self._closing or (automatic and guild.id in self._stopped_guilds):
                        raise UserError("Запит автопідключення скасовано: музику зупинено.")
                    if self.bot.get_guild(guild.id) is not guild:
                        raise UserError("Сервер більше не доступний для музичного підключення.")
                    if guild.id in self._teardowns:
                        raise UserError("Музичний плеєр ще завершує роботу. Зачекайте й повторіть.")
                    if not automatic:
                        self._stopped_guilds.discard(guild.id)
                    session = self.sessions.get(guild.id)
                    if session:
                        if session.backend.voice.channel.id != channel.id:
                            raise UserError("Бот уже працює в іншому голосовому каналі цього сервера.")
                        if not session.backend.is_connected():
                            raise UserError("Голосове з'єднання відновлюється. Повторіть пізніше.")
                        return session
                    if guild.voice_client is not None:
                        raise UserError("Сервер уже має інше голосове з'єднання бота.")
                    if len(self.sessions) >= self.options.max_players:
                        raise UserError("Досягнуто ліміт одночасних музичних серверів.")
                    voice = await channel.connect(timeout=20, reconnect=True,
                                                  cls=_OwnedVoiceClient,
                                                  self_deaf=self.options.self_deafen)
                    backend = FFmpegVoiceBackend(voice, self.executable,
                                                 self.options.default_volume)
                    if (self._closing or guild.id in self._stopped_guilds
                            or self.bot.get_guild(guild.id) is not guild):
                        await backend.close()
                        raise UserError("Музику зупинено; попереднє підключення скасовано.")
                    try:
                        player = self.manager.attach(guild.id, backend)
                    except BaseException:
                        await backend.close()
                        raise
                    session = Session(backend, player)
                    self.sessions[guild.id] = session
                    self._pause(guild.id)
                    return session
        except TimeoutError:
            raise UserError("Не вдалося вчасно підключитися до голосового каналу.") from None
        except discord.HTTPException:
            raise UserError("Discord не дозволив підключення. Перевірте дозволи каналу.") from None

    def _pause(self, guild_id):
        session = self.sessions.get(guild_id)
        if session and session.backend.voice.channel is not None:
            humans = any(not member.bot for member in session.backend.voice.channel.members)
            session.backend.set_paused(self.options.auto_pause and not humans)

    async def _remove(self, guild_id):
        task = None
        async with self._connections:
            session = self.sessions.get(guild_id)
            if session:
                task = self._teardowns.get(guild_id)
                if task is None:
                    async def teardown():
                        try:
                            try:
                                await self.manager.detach(guild_id)
                            finally:
                                await session.backend.close()
                        finally:
                            if self.sessions.get(guild_id) is session:
                                self.sessions.pop(guild_id, None)
                            self._teardowns.pop(guild_id, None)

                    task = asyncio.create_task(teardown(), name=f"music-disconnect-{guild_id}")
                    self._teardowns[guild_id] = task
                    def finished(done):
                        if not done.cancelled() and (error := done.exception()) is not None:
                            log.warning("Music teardown failed guild=%s type=%s", guild_id, type(error).__name__)

                    task.add_done_callback(finished)
        if task is not None:
            # A cancelled command must not abandon an owned voice connection.
            # Independent guild teardowns may finish concurrently.
            await asyncio.shield(task)

    @app_commands.command(name="play", description="Додати YouTube/SoundCloud трек або пошуковий запит")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, query: app_commands.Range[str, 1, 500]):
        guild_context(interaction)
        try:
            validate_source(query)
        except MusicError as error:
            raise UserError(str(error)) from None
        guild, channel, actor, _ = await self._context(interaction)
        session = await self._ensure(guild, channel)
        try:
            position = await session.player.enqueue(query, actor.id)
        except MusicError as error:
            raise UserError(str(error)) from None
        message = f"Запит додано до черги: {position}. Автоплейлист поступається замовленням."
        if spotify_track_id(query.strip()):
            message += " Spotify надає метадані; аудіо буде відповідним результатом YouTube."
        await self._reply(interaction, message)

    @app_commands.command(name="join", description="Підключити музику й автоплейлист; Керувати сервером")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def join(self, interaction: discord.Interaction):
        guild, channel, _, _ = await self._context(interaction, manager=True)
        await self._ensure(guild, channel)
        await self._reply(interaction, "Музичний плеєр підключено. Автоплейлист працює відповідно до налаштувань.")

    @app_commands.command(name="queue", description="Поточний трек і до 10 наступних замовлень")
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction):
        guild, _, _, _ = await self._context(interaction)
        session = self.sessions.get(guild.id)
        if not session:
            await self._reply(interaction, "Музичний плеєр не підключений. Використайте /music play.")
            return
        snapshot = session.player.snapshot()
        current = snapshot["current"]
        title = discord.utils.escape_mentions(discord.utils.escape_markdown(current.title[:180])) if current else "Немає"
        kind = "автоплейлист" if snapshot["autoplay"] else "замовлення"
        states = {"idle": "Очікування замовлень", "resolving": "Отримання треку",
                  "playing": "Відтворення", "reconnecting": "Відновлення з'єднання",
                  "cleanup": "Завершення попереднього аудіопотоку", "backoff": "Пауза перед наступною спробою",
                  "closed": "Зупинено", "disconnected": "З'єднання втрачено"}
        state = states.get(snapshot.get("state"), "Невідомо")
        lines = [f"Зараз: {title} ({kind}).", f"Стан: {state}."]
        for index, request in enumerate(snapshot["queue"][:10], 1):
            lines.append(f"{index}. Замовлення користувача `{request.requester_id}`")
        if snapshot.get("error"):
            code = normalize_failure_code(snapshot.get("error_code"))
            lines.append(f"Останній збій: {failure_message(code)}")
        lines.append(f"У черзі: {len(snapshot['queue'])}. Автоплейлист: {'увімкнено' if snapshot['autoplay_enabled'] else 'вимкнено'}.")
        await self._reply(interaction, "\n".join(lines))

    @app_commands.command(name="skip", description="Пропустити власний трек або автоплейлист")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction):
        guild, _, actor, _ = await self._context(interaction)
        session = self.sessions.get(guild.id)
        if not session:
            raise UserError("Музичний плеєр не підключений.")
        snapshot = session.player.snapshot()
        request = snapshot.get("request")
        if (request is not None and not snapshot["autoplay"]
                and request.requester_id != actor.id and not actor.guild_permissions.manage_guild):
            raise UserError("Можна пропустити власний трек; інші — з дозволом «Керувати сервером».")
        skipped = await session.player.skip()
        await self._reply(interaction, "Трек пропущено." if skipped else "Зараз немає активного треку.")

    @app_commands.command(name="stop", description="Очистити чергу й від'єднати бота; Керувати сервером")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction):
        guild, _, _, _ = await self._context(interaction, manager=True)
        self._stopped_guilds.add(guild.id)
        await self._remove(guild.id)
        await self._reply(interaction, "Музику зупинено, чергу очищено. Автопідключення вимкнено до нового запиту або перезапуску.")

    @app_commands.command(name="autoplay", description="Увімкнути чи вимкнути автоплейлист цієї сесії")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def autoplay(self, interaction: discord.Interaction, enabled: bool):
        guild, channel, _, _ = await self._context(interaction, manager=True)
        session = await self._ensure(guild, channel)
        await session.player.set_autoplay(enabled)
        await self._reply(interaction, f"Автоплейлист {'увімкнено' if enabled else 'вимкнено'} для поточної сесії.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if self.bot.user and member.id == self.bot.user.id and after.channel is None:
            session = self.sessions.get(member.guild.id)
            if session and not session.backend.is_connected():
                await self._remove(member.guild.id)
        self._pause(member.guild.id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        await self._remove(guild.id)
        self._stopped_guilds.discard(guild.id)

    @tasks.loop(seconds=60)
    async def autojoin(self):
        for channel_id in self.options.autojoin_channels:
            channel = self.bot.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel) or channel.guild.unavailable:
                continue
            guild = channel.guild
            if guild.id in self._stopped_guilds or guild.id in self.sessions:
                continue
            try:
                if guild.afk_channel and channel.id == guild.afk_channel.id:
                    continue
                async with asyncio.timeout(10):
                    bot_member = await guild.fetch_member(self.bot.user.id)
                self._permissions(channel, bot_member, bot_member)
                await self._ensure(guild, channel, automatic=True)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.warning("Music autojoin failed guild=%s type=%s", guild.id, type(error).__name__)

    @autojoin.before_loop
    async def before_autojoin(self):
        await self.bot.wait_until_ready()
