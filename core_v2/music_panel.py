"""Short-lived private Discord music controls with fresh permissions per click."""
import asyncio
import logging

import discord

from .music_engine import MusicError
from .security import UserError

log = logging.getLogger(__name__)
MENTIONS = discord.AllowedMentions.none()
PANEL_TIMEOUT = 120
MAX_PANELS = 64


async def _private(interaction, text):
    send = (interaction.followup.send if interaction.response.is_done()
            else interaction.response.send_message)
    await send(text, ephemeral=True, allowed_mentions=MENTIONS)


class MusicPanel(discord.ui.View):
    """A panel is bound to one author, guild, channel and exact session."""

    def __init__(self, cog, guild_id, user_id, channel_id, session):
        super().__init__(timeout=PANEL_TIMEOUT)
        self.cog = cog
        self.guild_id, self.user_id, self.channel_id = guild_id, user_id, channel_id
        self.session = session
        self.message = None
        self.expired = False
        self._lock = asyncio.Lock()

    def render(self):
        guild = self.cog.bot.get_guild(self.guild_id)
        if guild is None:
            status = "Сервер недоступний. Відкрийте /music panel знову."
        elif self.cog.sessions.get(self.guild_id) is not self.session:
            status = "Сесію завершено. Відкрийте /music panel знову."
        else:
            try:
                status = self.cog.queue_text(guild)
            except Exception as error:
                log.warning("Music panel status failed guild=%s type=%s",
                            self.guild_id, type(error).__name__)
                status = "Стан плеєра невідомий. Перевірте /music queue."
        mode = "off"
        player = getattr(self.session, "player", None)
        if player is not None:
            try:
                mode = player.snapshot().get("repeat_mode", "off")
            except Exception:
                pass
        self.repeat_button.label = {"off": "Повтор: вимк.", "one": "Повтор: трек",
                                    "all": "Повтор: черга"}.get(mode, "Повтор")
        embed = discord.Embed(title="🎵 YEBOYBot · Плеєр", description=status[:3900],
                              colour=0x6657C8)
        try:
            volume = self.session.backend.volume_state().get("configured")
            if type(volume) in (int, float) and 0 <= volume <= 1:
                embed.add_field(name="🔊 Гучність", value=f"{round(volume * 100)}%", inline=True)
        except Exception:
            pass
        embed.set_footer(text="Приватна панель · 2 хвилини · Повтор: вимк. → трек → черга")
        return embed

    async def interaction_check(self, interaction):
        if (getattr(interaction, "guild_id", None) != self.guild_id
                or getattr(interaction, "channel_id", None) != self.channel_id
                or interaction.user.id != self.user_id):
            await _private(interaction, "Ця панель належить її автору в початковому каналі.")
            return False
        if self.expired or self.cog._closing:
            await _private(interaction, "Панель застаріла. Відкрийте /music panel знову.")
            return False
        return True

    def close_locally(self):
        self.expired = True
        for item in self.children:
            item.disabled = True
        self.stop()
        self.cog.panels.discard(self)

    async def expire(self):
        if self.expired:
            return
        self.close_locally()
        if self.message is not None:
            try:
                async with asyncio.timeout(5):
                    await self.message.edit(view=self, allowed_mentions=MENTIONS)
            except Exception as error:
                log.info("Music panel expiry edit failed type=%s", type(error).__name__)

    async def on_timeout(self):
        await self.expire()

    async def on_error(self, interaction, error, item):
        log.warning("Music panel failed type=%s", type(error).__name__)
        try:
            await _private(interaction, "Не вдалося виконати дію. Перевірте /music queue.")
        except discord.HTTPException:
            pass

    async def _action(self, interaction, action):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer()
        try:
            async with self._lock:
                if self.expired or self.cog._closing:
                    raise UserError("Панель застаріла. Відкрийте /music panel знову.")
                guild = interaction.guild
                if (guild is None or guild.unavailable
                        or self.cog.bot.get_guild(self.guild_id) is not guild):
                    raise UserError("Сервер більше не доступний.")
                # Reject stale session *before* inspecting another voice client,
                # then recheck again after the REST member lookup can yield.
                if self.cog.sessions.get(self.guild_id) is not self.session:
                    raise UserError("Панель застаріла. Відкрийте /music panel знову.")
                manager = action in {"pause", "resume", "repeat", "shuffle", "stop",
                                     "volume_down", "volume_up"}
                async with asyncio.timeout(12):
                    guild, _, actor, _ = await self.cog.voice_context(
                        guild, interaction.user.id, manager=manager,
                        allow_closing=action in {"refresh", "stop"},
                        cleanup=action == "stop")
                if self.expired or self.cog._closing:
                    raise UserError("Панель застаріла. Відкрийте /music panel знову.")
                if self.cog.sessions.get(self.guild_id) is not self.session:
                    raise UserError("Панель застаріла. Відкрийте /music panel знову.")
                session = self.session
                if action not in {"stop", "refresh"} and self.cog._control_session(guild) is not session:
                    raise UserError("Панель застаріла. Відкрийте /music panel знову.")

                if action in {"pause", "resume"}:
                    active = action == "pause"
                    changed = await session.player.set_manual_paused(active)
                    word = "ввімкнено" if active else "вимкнено"
                    notice = f"Ручну паузу {word}." if changed else f"Ручну паузу вже {word}."
                elif action == "skip":
                    state = session.player.snapshot()
                    request = state.get("request")
                    if (request is not None and not state.get("autoplay")
                            and request.requester_id != actor.id
                            and not actor.guild_permissions.manage_guild):
                        raise UserError("Чужі треки можна пропускати лише з дозволом «Керувати сервером».")
                    skipped = await session.player.skip()
                    notice = "Трек пропущено." if skipped else "Активного треку немає."
                elif action == "shuffle":
                    count = await session.player.shuffle_queue()
                    notice = (f"Перемішано {count} замовлень." if count >= 2 else
                              "Для перемішування потрібні два замовлення.")
                elif action == "repeat":
                    previous = session.player.snapshot().get("repeat_mode", "off")
                    mode = {"off": "one", "one": "all", "all": "off"}.get(previous, "off")
                    await session.player.set_repeat(mode)
                    notice = "Режим повтору: " + {"off": "вимкнено", "one": "трек",
                                                    "all": "уся черга"}[mode] + "."
                elif action in {"volume_down", "volume_up"}:
                    state = session.backend.volume_state()
                    current_gain = state.get("configured") if isinstance(state, dict) else None
                    if type(current_gain) not in (int, float) or not 0 <= current_gain <= 1:
                        raise UserError("Поточну гучність не визначено. Скористайтеся /music volume.")
                    percent = round(current_gain * 100)
                    step = -10 if action == "volume_down" else 10
                    target = max(0, min(100, percent + step))
                    changed = await session.player.set_volume(target)
                    notice = (f"Гучність: {target}%. " +
                              ("Змінено." if changed else "Уже на цій межі."))
                elif action == "stop":
                    self.cog._stopped_guilds.add(guild.id)
                    confirmed = await self.cog._remove(guild.id, expected_session=session)
                    notice = ("Музичну сесію завершено." if confirmed is True else
                              "Зупинку не підтверджено. Скористайтеся /music stop.")
                    self.close_locally()
                else:
                    notice = "Стан плеєра оновлено."
                try:
                    async with asyncio.timeout(6):
                        await interaction.edit_original_response(
                            embed=self.render(), view=self, allowed_mentions=MENTIONS)
                except (discord.HTTPException, TimeoutError):
                    log.info("Music panel refresh failed guild=%s", self.guild_id)
                await interaction.followup.send(notice, ephemeral=True, allowed_mentions=MENTIONS)
        except (UserError, MusicError) as error:
            await _private(interaction, str(error))
        except TimeoutError:
            await _private(interaction, "Тайм-аут. Перевірте /music queue перед повторною спробою.")
        except Exception as error:
            log.warning("Music panel action failed guild=%s type=%s",
                        self.guild_id, type(error).__name__)
            await _private(interaction, "Не вдалося підтвердити дію. Перевірте /music queue.")

    @discord.ui.button(label="Пауза", emoji="⏸️", style=discord.ButtonStyle.secondary, row=0)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._action(interaction, "pause")

    @discord.ui.button(label="Продовжити", emoji="▶️", style=discord.ButtonStyle.secondary, row=0)
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._action(interaction, "resume")

    @discord.ui.button(label="Пропустити", emoji="⏭️", style=discord.ButtonStyle.primary, row=0)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._action(interaction, "skip")

    @discord.ui.button(label="Мікс", emoji="🔀", style=discord.ButtonStyle.secondary, row=0)
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._action(interaction, "shuffle")

    @discord.ui.button(label="Повтор: вимк.", emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def repeat_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._action(interaction, "repeat")

    @discord.ui.button(label="−10%", emoji="🔉", style=discord.ButtonStyle.secondary, row=1)
    async def volume_down_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._action(interaction, "volume_down")

    @discord.ui.button(label="+10%", emoji="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def volume_up_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._action(interaction, "volume_up")

    @discord.ui.button(label="Оновити", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._action(interaction, "refresh")

    @discord.ui.button(label="Стоп", emoji="⏹️", style=discord.ButtonStyle.danger, row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._action(interaction, "stop")
