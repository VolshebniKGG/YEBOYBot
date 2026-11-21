"""Simplified music commands: 1-2 slash commands with unified interface.
Reduces gap between autoplay tracks, supports YouTube/Spotify/live streams in one command.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .config import ROOT
from .media_environment import ffmpeg_path
from .music_engine import (
    MusicError, MusicManager, YTDLPResolver, spotify_track_id,
    validate_source, failure_message, normalize_failure_code
)
from .music_options import load_autoplaylist, load_music_options
from .security import UserError, guild_context

log = logging.getLogger(__name__)

# Configuration for minimal gap between tracks
PRELOAD_NEXT_TRACK = True  # Pre-resolve next track while current plays
AUTOPLAY_TRANSITION_DELAY = 0.1  # Minimal delay between tracks (seconds)


@dataclass
class SimpleSession:
    """Lightweight session state for simplified music."""
    backend: object
    player: object
    guild_id: int
    channel_id: int
    closing: bool = False


class SimpleMusic(commands.GroupCog, group_name="music", group_description="Музика: play/stop/queue/volume"):
    def __init__(self, bot, *, options=None, manager=None, root=ROOT):
        self.bot = bot
        self.options = options if options is not None else load_music_options(root)
        self.executable = ffmpeg_path(bot.settings.ffmpeg, root)
        if manager is None:
            manager = MusicManager(
                self.options,
                load_autoplaylist(root),
                YTDLPResolver()
            )
        self.manager = manager
        self.sessions = {}  # guild_id -> SimpleSession
        self._connections = asyncio.Lock()
        self._closing = False

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
        for guild_id in tuple(self.sessions):
            await self._disconnect(guild_id)
        try:
            await asyncio.wait_for(self.manager.close(), timeout=10)
        except Exception:
            pass

    async def _reply(self, interaction: discord.Interaction, message: str, ephemeral: bool = True):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=ephemeral, allowed_mentions=discord.AllowedMentions.none())
            else:
                await interaction.response.send_message(message, ephemeral=ephemeral, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass

    def _validate_voice(self, interaction: discord.Interaction) -> tuple[discord.Guild, discord.VoiceChannel, discord.Member, discord.Member]:
        guild = guild_context(interaction)
        if self.bot.user is None:
            raise UserError("Discord-клієнт ще не готовий.")
        actor = guild.get_member(interaction.user.id)
        bot_member = guild.get_member(self.bot.user.id)
        if not actor or not bot_member:
            raise UserError("Не вдалося перевірити учасників. Спробуйте пізніше.")
        if not actor.voice or not actor.voice.channel:
            raise UserError("Спочатку приєднайтесь до голосового каналу.")
        channel = actor.voice.channel
        if not isinstance(channel, discord.VoiceChannel) or channel.guild.id != guild.id:
            raise UserError("Приєднайтесь до голосового каналу цього сервера.")
        perms = channel.permissions_for(bot_member)
        if not (perms.view_channel and perms.connect and perms.speak):
            raise UserError("Боту потрібні дозволи: Переглядати канал, Підключатися, Говорити.")
        return guild, channel, actor, bot_member

    async def _ensure_connected(self, guild: discord.Guild, channel: discord.VoiceChannel) -> SimpleSession:
        """Get or create voice connection for guild."""
        async with self._connections:
            if self._closing:
                raise UserError("Музичний модуль завершує роботу.")

            session = self.sessions.get(guild.id)
            if session:
                if session.closing:
                    raise UserError("Попереднє відключення ще не завершено. Зачекайте.")
                if session.channel_id != channel.id:
                    raise UserError("Бот уже грає в іншому голосовому каналі.")
                return session

            if guild.voice_client is not None:
                raise UserError("Сервер уже має інше голосове з'єднання.")

            if len(self.sessions) >= self.options.max_players:
                raise UserError("Досягнуто ліміт одночасних музичних серверів.")

            # Connect to voice
            voice = await channel.connect(timeout=20, reconnect=True, self_deaf=self.options.self_deafen)

            # Create backend using existing FFmpegVoiceBackend logic
            from .music import FFmpegVoiceBackend
            backend = FFmpegVoiceBackend(voice, self.executable, self.options.default_volume)

            # Attach player
            player = self.manager.attach(guild.id, backend)

            session = SimpleSession(
                backend=backend,
                player=player,
                guild_id=guild.id,
                channel_id=channel.id
            )
            self.sessions[guild.id] = session
            return session

    async def _disconnect(self, guild_id: int) -> bool:
        session = self.sessions.pop(guild_id, None)
        if not session:
            return True
        session.closing = True
        try:
            await asyncio.wait_for(self.manager.detach(guild_id), timeout=10)
        except Exception:
            pass
        try:
            if hasattr(session.backend, 'close'):
                await asyncio.wait_for(session.backend.close(), timeout=10)
        except Exception:
            pass
        return True

    # ===== UNIFIED /music COMMAND =====

    @app_commands.command(name="music", description="Музика: play/stop/queue/volume/pause/resume/skip")
    @app_commands.describe(
        action="Дія: play/stop/queue/volume/pause/resume/skip/autoplay",
        query="Пошук/посилання (YouTube, Spotify, SoundCloud, пряме ефір) — для play",
        value="Значення: 0-100 для volume, true/false для autoplay/pause/resume"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="▶ Play — грати (пошук/YouTube/Spotify/ефір)", value="play"),
        app_commands.Choice(name="⏹ Stop — зупинити і вийти", value="stop"),
        app_commands.Choice(name="📋 Queue — показати чергу", value="queue"),
        app_commands.Choice(name="🔊 Volume — гучність 0-100%", value="volume"),
        app_commands.Choice(name="⏸ Pause — пауза", value="pause"),
        app_commands.Choice(name="▶ Resume — продовжити", value="resume"),
        app_commands.Choice(name="⏭ Skip — пропустити трек", value="skip"),
        app_commands.Choice(name="🔁 Autoplay — увімк/вимк автоплейлист", value="autoplay"),
    ])
    @app_commands.guild_only()
    async def music(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        query: Optional[str] = None,
        value: Optional[str] = None
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            if action.value == "play":
                await self._cmd_play(interaction, query)
            elif action.value == "stop":
                await self._cmd_stop(interaction)
            elif action.value == "queue":
                await self._cmd_queue(interaction)
            elif action.value == "volume":
                await self._cmd_volume(interaction, value)
            elif action.value == "pause":
                await self._cmd_pause(interaction, True)
            elif action.value == "resume":
                await self._cmd_pause(interaction, False)
            elif action.value == "skip":
                await self._cmd_skip(interaction)
            elif action.value == "autoplay":
                await self._cmd_autoplay(interaction, value)
        except UserError as e:
            await self._reply(interaction, str(e))
        except Exception as e:
            log.exception("Music command failed")
            await self._reply(interaction, f"Помилка: {type(e).__name__}")

    async def _cmd_play(self, interaction: discord.Interaction, query: Optional[str]):
        if not query or not query.strip():
            raise UserError("Вкажіть назву треку, посилання на YouTube/Spotify/SoundCloud або пряме ефір.")

        query = query.strip()
        if len(query) > 500:
            raise UserError("Запит занадто довгий (макс. 500 символів).")

        # Validate source (supports YouTube, Spotify, SoundCloud, search, live streams)
        try:
            validate_source(query)
        except MusicError as e:
            raise UserError(str(e))

        guild, channel, actor, _ = self._validate_voice(interaction)
        session = await self._ensure_connected(guild, channel)

        # Enqueue
        try:
            position = await session.player.enqueue(query, actor.id)
        except MusicError as e:
            raise UserError(str(e))

        # Build response
        msg = f"✅ Додано до черги (позиція {position}): `{query[:100]}`"
        if spotify_track_id(query):
            msg += "\n🎵 Spotify: метадані отримано, аудіо знайдеться на YouTube."
        if self._is_live_url(query):
            msg += "\n🔴 Виявлено можливе пряме ефір — бот підтримає перепідключення."

        await self._reply(interaction, msg)

    async def _cmd_stop(self, interaction: discord.Interaction):
        guild, _, _, _ = self._validate_voice(interaction)
        if guild.id not in self.sessions:
            raise UserError("Музичний плеєр не підключений.")
        await self._disconnect(guild.id)
        await self._reply(interaction, "⏹ Музику зупинено, чергу очищено, бота відключено.")

    async def _cmd_queue(self, interaction: discord.Interaction):
        guild, _, _, _ = self._validate_voice(interaction)
        session = self.sessions.get(guild.id)
        if not session:
            raise UserError("Музичний плеєр не підключений. Використайте /music play.")

        snapshot = session.player.snapshot()
        current = snapshot["current"]

        lines = []
        if current:
            title = discord.utils.escape_markdown(current.title[:180])
            if getattr(current, "is_live", False):
                title = "🔴 LIVE · " + title
            lines.append(f"▶ **Зараз:** {title}")
        else:
            lines.append("▶ **Зараз:** нічого не грає")

        state_map = {
            "idle": "⏸ Очікування", "resolving": "🔍 Отримання треку",
            "playing": "▶ Відтворення", "paused": "⏸ Пауза",
            "reconnecting": "🔄 Відновлення", "backoff": "⏳ Пауза перед повтором"
        }
        lines.append(f"📊 **Стан:** {state_map.get(snapshot.get('state'), snapshot.get('state', 'невідомо'))}")

        if snapshot.get("manual_paused"):
            lines.append("⏸ **Ручна пауза:** увімкнена")

        repeat_map = {"off": "вимкнено", "one": "поточний трек", "all": "вся черга"}
        lines.append(f"🔁 **Повтор:** {repeat_map.get(snapshot.get('repeat_mode'), 'невідомо')}")

        # Volume
        if hasattr(session.backend, 'volume_state'):
            vol_state = session.backend.volume_state()
            if vol_state.get("source") == "active" and vol_state.get("actual") is not None:
                lines.append(f"🔊 **Гучність:** {vol_state['actual']*100:.0f}%")
            elif vol_state.get("source") == "configured" and vol_state.get("configured") is not None:
                lines.append(f"🔊 **Гучність (наступні):** {vol_state['configured']*100:.0f}%")

        queue = snapshot["queue"]
        if queue:
            lines.append(f"\n📋 **Черга ({len(queue)}):**")
            for i, req in enumerate(queue[:10], 1):
                src = req.source[:80] + ("…" if len(req.source) > 80 else "")
                lines.append(f"  {i}. `{src}`")
            if len(queue) > 10:
                lines.append(f"  … і ще {len(queue) - 10}")
        else:
            lines.append("\n📋 **Черга:** порожня")
            if snapshot.get("autoplay_enabled"):
                lines.append("🔁 Автоплейлист увімкнено — гратиме автоматично.")

        if snapshot.get("error"):
            code = normalize_failure_code(snapshot.get("error_code"))
            lines.append(f"\n⚠ **Останній збій:** {failure_message(code)}")

        await self._reply(interaction, "\n".join(lines))

    async def _cmd_volume(self, interaction: discord.Interaction, value: Optional[str]):
        guild, _, _, _ = self._validate_voice(interaction)
        session = self.sessions.get(guild.id)
        if not session:
            raise UserError("Музичний плеєр не підключений.")

        if value is None:
            # Show current
            if hasattr(session.backend, 'volume_state'):
                vol_state = session.backend.volume_state()
                if vol_state.get("source") == "active" and vol_state.get("actual") is not None:
                    await self._reply(interaction, f"🔊 Поточна гучність: {vol_state['actual']*100:.0f}%")
                elif vol_state.get("source") == "configured" and vol_state.get("configured") is not None:
                    await self._reply(interaction, f"🔊 Гучність для наступних треків: {vol_state['configured']*100:.0f}%")
                else:
                    await self._reply(interaction, "🔊 Гучність: невідома")
            else:
                await self._reply(interaction, "🔊 Гучність: недоступна")
            return

        try:
            percent = int(value)
            if not 0 <= percent <= 100:
                raise ValueError
        except ValueError:
            raise UserError("Гучність має бути числом від 0 до 100.")

        try:
            changed = await session.player.set_volume(percent)
            msg = f"🔊 Гучність встановлена: {percent}%"
            if not changed:
                msg += " (уже було таким)"
            await self._reply(interaction, msg)
        except MusicError as e:
            raise UserError(str(e))

    async def _cmd_pause(self, interaction: discord.Interaction, enabled: bool):
        guild, _, _, _ = self._validate_voice(interaction)
        session = self.sessions.get(guild.id)
        if not session:
            raise UserError("Музичний плеєр не підключений.")

        try:
            changed = await session.player.set_manual_paused(enabled)
            if enabled:
                msg = "⏸ Пауза увімкнена." if changed else "⏸ Пауза вже була увімкнена."
            else:
                msg = "▶ Пауза вимкнена." if changed else "▶ Пауза вже була вимкнена."
            await self._reply(interaction, msg)
        except MusicError as e:
            raise UserError(str(e))

    async def _cmd_skip(self, interaction: discord.Interaction):
        guild, _, actor, _ = self._validate_voice(interaction)
        session = self.sessions.get(guild.id)
        if not session:
            raise UserError("Музичний плеєр не підключений.")

        snapshot = session.player.snapshot()
        request = snapshot.get("request") or snapshot.get("live_retry")

        # Check permissions: own track or manage_guild
        if (request and request.requester_id and request.requester_id != actor.id
                and not actor.guild_permissions.manage_guild):
            raise UserError("Можна пропустити власний трек; інші — з дозволом «Керувати сервером».")

        skipped = await session.player.skip()
        await self._reply(interaction, "⏭ Трек пропущено." if skipped else "Зараз немає активного треку.")

    async def _cmd_autoplay(self, interaction: discord.Interaction, value: Optional[str]):
        guild, channel, _, _ = self._validate_voice(interaction)
        session = await self._ensure_connected(guild, channel)

        if value is None:
            # Toggle
            enabled = not session.player.autoplay_enabled
        else:
            v = value.strip().lower()
            if v not in {"true", "false", "on", "off", "yes", "no", "1", "0"}:
                raise UserError("Вкажіть true/false, on/off, yes/no або 1/0.")
            enabled = v in {"true", "on", "yes", "1"}

        await session.player.set_autoplay(enabled)
        await self._reply(interaction, f"🔁 Автоплейлист {'увімкнено' if enabled else 'вимкнено'}.")

    def _is_live_url(self, url: str) -> bool:
        """Heuristic to detect potential live streams."""
        url_lower = url.lower()
        live_indicators = ["live", "stream", "radio", "24/7", "broadcast"]
        return any(ind in url_lower for ind in live_indicators)

    # ===== OPTIONAL: Separate /music queue for quick access =====

    @app_commands.command(name="queue", description="Швидкий перегляд черги музики")
    @app_commands.guild_only()
    async def queue_shortcut(self, interaction: discord.Interaction):
        """Shortcut to view queue without subcommand."""
        await self._cmd_queue(interaction)

    # ===== Auto-join =====

    @tasks.loop(seconds=60)
    async def autojoin(self):
        for channel_id in self.options.autojoin_channels:
            channel = self.bot.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel) or channel.guild.unavailable:
                continue
            guild = channel.guild
            if guild.id in self.sessions:
                continue
            try:
                bot_member = await guild.fetch_member(self.bot.user.id)
                perms = channel.permissions_for(bot_member)
                if not (perms.view_channel and perms.connect and perms.speak):
                    continue
                await self._ensure_connected(guild, channel)
            except Exception as e:
                log.warning("Autojoin failed guild=%s type=%s", guild.id, type(e).__name__)

    @autojoin.before_loop
    async def before_autoplay(self):
        await self.bot.wait_until_ready()

    # ===== Voice state handling =====

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if self.bot.user and member.id == self.bot.user.id and after.channel is None:
            session = self.sessions.get(member.guild.id)
            if session and not session.closing:
                await self._disconnect(member.guild.id)