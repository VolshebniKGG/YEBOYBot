import asyncio
import datetime
import logging
from io import BytesIO
import time
import unicodedata

import discord
from discord import app_commands
from discord.ext import commands

from .security import UserError, permission, target_check, respond
from .security import guild_context
from .moderation import ModerationService, ModerationError, valid_id
from .discord_moderation import DiscordGateway, history_embed, warning_embeds
from .clear_commands import preview as clear_preview, status as clear_status
from .rank_card import render_rank_card


log = logging.getLogger(__name__)
RANK_DB_TIMEOUT = 10
RANK_ASSET_TIMEOUT = 8
RANK_RENDER_TIMEOUT = 10
RANK_SLOT_TIMEOUT = 2
RANK_ASSET_LIMIT = 2 * 1024 * 1024
RANK_PNG_LIMIT = 512 * 1024
LEADERBOARD_DB_TIMEOUT = 10


async def _rank_asset(asset, size):
    """Read only SDK-supplied Discord CDN assets; unavailable icons use a fallback."""
    if asset is None:
        return None
    try:
        async with asyncio.timeout(RANK_ASSET_TIMEOUT):
            result = await asset.with_size(size).with_static_format("png").read()
        if isinstance(result, bytes) and len(result) <= RANK_ASSET_LIMIT:
            return result
    except (discord.HTTPException, OSError, ValueError, TypeError, TimeoutError):
        log.info("Rank asset unavailable; using fallback")
    return None


def moderation_user_id(value):
    """Parse bounded ASCII snowflakes before they can reach SQL or Discord."""
    if not isinstance(value, str) or not 1 <= len(value) <= 19 or not value.isascii() or not value.isdecimal():
        raise ModerationError("Вкажіть числовий Discord ID від 1 до 19 цифр.")
    return valid_id(int(value))


async def moderation_response(interaction, result):
    state = {"pending": "Непідтверджено", "applied": "Застосовано",
             "failed": "Зафіксовано помилку", "revoked": "Відкликано"}.get(result.status)
    message = f"{result.message}\nID справи: `{result.case_id}`"
    if state:
        message += f"\nЗбережений стан: {state}."
    await interaction.followup.send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class Core(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.moderation = ModerationService(bot.db, DiscordGateway(bot))
        self._clear_service = None
        self._rank_slots = asyncio.Semaphore(2)
        self._rank_workers = set()

    def _clear_domain(self):
        # Existing commands need no manual-job repository; initialize on demand.
        if self._clear_service is None:
            from .clear_service import ClearService
            from .clear_repository import ClearRepository
            from .discord_clear import ClearGateway
            self._clear_service = ClearService(ClearRepository(self.bot.db), ClearGateway(self.bot))
        return self._clear_service

    @app_commands.command(name="clear", description="Переглянути до 25 повідомлень для підтвердженого очищення")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    @app_commands.describe(amount="Переглянути від 1 до 25 останніх повідомлень перед командою",
                           reason="Причина від 1 до 200 символів")
    async def clear(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 25],
                    reason: app_commands.Range[str, 1, 200]):
        guild_context(interaction)
        await clear_preview(interaction, self._clear_domain(), amount, reason)

    @app_commands.command(name="clear-status", description="Перевірити власний запит очищення в цьому каналі")
    @app_commands.guild_only()
    @app_commands.describe(request_id="32-символьний ID вашого запиту очищення")
    async def clear_status(self, interaction: discord.Interaction,
                           request_id: app_commands.Range[str, 32, 32]):
        guild_context(interaction)
        await clear_status(interaction, self._clear_domain(), request_id)

    @app_commands.command(name="kick", description="Вигнати учасника з перевіркою прав та записом справи")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.guild_only()
    async def kick(self, interaction: discord.Interaction, member: discord.Member,
                   reason: app_commands.Range[str, 1, 500]):
        guild = guild_context(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.moderation.kick(guild.id, interaction.user.id, member.id, interaction.id, reason)
        except ModerationError as error:
            raise UserError(str(error)) from None
        await moderation_response(interaction, result)

    async def _ban_action(self, interaction, action, user_id, reason):
        guild = guild_context(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            target_id = moderation_user_id(user_id)
            result = await getattr(self.moderation, action)(guild.id, interaction.user.id,
                                                          target_id, interaction.id, reason)
        except ModerationError as error:
            raise UserError(str(error)) from None
        await moderation_response(interaction, result)

    @app_commands.command(name="ban", description="Заблокувати користувача за ID без видалення повідомлень")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    @app_commands.describe(user_id="Числовий Discord ID, зокрема користувача поза сервером",
                           reason="Обов'язкова причина блокування")
    async def ban(self, interaction: discord.Interaction, user_id: app_commands.Range[str, 1, 19],
                  reason: app_commands.Range[str, 1, 500]):
        await self._ban_action(interaction, "ban", user_id, reason)

    @app_commands.command(name="unban", description="Зняти блокування користувача за ID із записом справи")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    @app_commands.describe(user_id="Числовий Discord ID заблокованого користувача",
                           reason="Обов'язкова причина зняття блокування")
    async def unban(self, interaction: discord.Interaction, user_id: app_commands.Range[str, 1, 19],
                    reason: app_commands.Range[str, 1, 500]):
        await self._ban_action(interaction, "unban", user_id, reason)

    @app_commands.command(name="modhistory", description="Історія справ цього сервера, зокрема непідтверджених")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    @app_commands.choices(state=[app_commands.Choice(name="Непідтверджено", value="pending"),
                                app_commands.Choice(name="Застосовано", value="applied"),
                                app_commands.Choice(name="Зафіксовано помилку", value="failed"),
                                app_commands.Choice(name="Відкликано", value="revoked")])
    async def modhistory(self, interaction: discord.Interaction, user_id: app_commands.Range[str, 1, 19] | None = None,
                         state: app_commands.Choice[str] | None = None,
                         page: app_commands.Range[int, 1, 1000] = 1):
        guild = guild_context(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            rows = await self.moderation.history(guild.id, interaction.user.id,
                user_id=None if user_id is None else moderation_user_id(user_id),
                status=None if state is None else state.value, page=page)
        except ModerationError as error:
            raise UserError(str(error)) from None
        await interaction.followup.send(embed=history_embed(rows, page), ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="status", description="Стан ядра та бази даних")
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        healthy = await self.bot.db.health()
        await respond(interaction, f"Core v2 · DB: {healthy} · Серверів: {len(self.bot.guilds)}")

    @app_commands.command(name="levels", description="Увімкнути або вимкнути текстовий і голосовий XP")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def levels(self, interaction: discord.Interaction, enabled: bool):
        guild = permission(interaction, "manage_guild")
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.configure(guild.id, levels=enabled)
        self.bot.settings_cache.pop(guild.id, None)
        voice_xp = self.bot.get_cog("VoiceXP") if hasattr(self.bot, "get_cog") else None
        if voice_xp is not None:
            voice_xp.reset_guild(guild.id)
        await respond(interaction, f"Текстовий і голосовий XP: {'увімкнено' if enabled else 'вимкнено'}.")

    async def _render_rank(self, **values):
        # A timed-out/cancelled waiter must not free the slot while its thread is
        # still rendering. Keep a strong task reference until actual completion.
        async with asyncio.timeout(RANK_SLOT_TIMEOUT):
            await self._rank_slots.acquire()
        try:
            worker = asyncio.create_task(asyncio.to_thread(render_rank_card, **values))
        except BaseException:
            self._rank_slots.release()
            raise
        self._rank_workers.add(worker)

        def completed(task):
            self._rank_workers.discard(task)
            self._rank_slots.release()
            if not task.cancelled():
                task.exception()  # consume an error if the original waiter left

        worker.add_done_callback(completed)
        async with asyncio.timeout(RANK_RENDER_TIMEOUT):
            result = await asyncio.shield(worker)
        if not isinstance(result, bytes) or len(result) > RANK_PNG_LIMIT:
            raise ValueError("Invalid rank image output")
        return result

    @app_commands.command(name="rank", description="Картка текстового та голосового рангу учасника")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None):
        guild = guild_context(interaction)
        await interaction.response.defer()
        member = member or interaction.user
        if member.guild.id != guild.id:
            raise UserError("Учасник має належати цьому серверу.")
        try:
            async with asyncio.timeout(RANK_DB_TIMEOUT):
                row = await self.bot.db.rank_snapshot(guild.id, member.id)
        except Exception:
            log.warning("Rank database read failed guild=%s", guild.id)
            await interaction.followup.send("Не вдалося прочитати XP. Спробуйте пізніше.",
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return
        avatar, icon = await asyncio.gather(_rank_asset(member.display_avatar, 256),
                                           _rank_asset(guild.icon, 64))
        try:
            image = await self._render_rank(display_name=member.display_name,
                text_xp=row["text_xp"], voice_xp=row["voice_xp"],
                text_rank=row["text_rank"], voice_rank=row["voice_rank"],
                avatar_bytes=avatar, guild_icon_bytes=icon)
        except Exception:
            log.warning("Rank card rendering failed guild=%s", guild.id)
            await interaction.followup.send("Не вдалося створити картку рангу. Спробуйте пізніше.",
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return
        text_place = row["text_rank"] or "—"
        voice_place = row["voice_rank"] or "—"
        clean_name = "".join(character for character in member.display_name[:128]
                             if not unicodedata.category(character).startswith("C"))
        name = discord.utils.escape_mentions(discord.utils.escape_markdown(clean_name))[:100]
        embed = discord.Embed(title=f"Ранг: {name}", description=(
            f'Чат: {row["text_xp"]} XP · місце {text_place}\n'
            f'Голос: {row["voice_xp"]} XP · місце {voice_place}'))
        embed.set_image(url="attachment://rank.png")
        await interaction.followup.send(embed=embed,
            file=discord.File(BytesIO(image), filename="rank.png"),
            allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="leaderboard", description="Рейтинг загального, текстового або голосового XP")
    @app_commands.guild_only()
    @app_commands.choices(xp_type=[app_commands.Choice(name="Загальний", value="total"),
                                  app_commands.Choice(name="Чат", value="text"),
                                  app_commands.Choice(name="Голос", value="voice")])
    @app_commands.describe(page="Сторінка рейтингу від 1 до 1000",
                           xp_type="Окремий рейтинг чату, голосу або сума XP")
    async def leaderboard(self, interaction: discord.Interaction,
                          page: app_commands.Range[int, 1, 1000] = 1,
                          xp_type: app_commands.Choice[str] | None = None):
        guild = guild_context(interaction)
        guild_id = guild.id
        if (type(guild_id) is not int or not 0 < guild_id < 2**63 or
                type(page) is not int or not 1 <= page <= 1000 or
                (xp_type is not None and not isinstance(xp_type, app_commands.Choice))):
            raise UserError("Вкажіть сторінку від 1 до 1000 і доступний тип XP.")
        kind = "total" if xp_type is None else xp_type.value
        if type(kind) is not str or kind not in {"total", "text", "voice"}:
            raise UserError("Виберіть загальний, текстовий або голосовий XP.")

        def current_guild():
            get_guild = getattr(self.bot, "get_guild", None)
            return (interaction.guild is guild and type(interaction.guild_id) is int and
                    interaction.guild_id == guild_id and guild.id == guild_id and
                    not guild.unavailable and callable(get_guild) and get_guild(guild_id) is guild)

        if not current_guild():
            raise UserError("Сервер більше не доступний. Повторіть команду після підключення.")
        # The first followup inherits the public defer visibility in Discord.
        # Error replies therefore contain fixed safe text and are public too.
        await interaction.response.defer()
        try:
            async with asyncio.timeout(LEADERBOARD_DB_TIMEOUT):
                if kind == "total":
                    rows = await self.bot.db.leaderboard(guild_id, page)
                else:
                    rows = await self.bot.db.leaderboard(guild_id, page, kind=kind)
            lines = []
            # Only validated integers enter the public embed. Never render
            # arbitrary SQL fields, display names or an unbounded result set.
            for index, row in enumerate(rows[:10], 1):
                user_id, text_xp, voice_xp = row["user_id"], row["text_xp"], row["voice_xp"]
                if (type(user_id) is not int or not 0 < user_id < 2**63 or
                        any(type(value) is not int or not 0 <= value < 2**63
                            for value in (text_xp, voice_xp))):
                    raise ValueError("Invalid stored XP profile")
                value = text_xp + voice_xp if kind == "total" else row[f"{kind}_xp"]
                lines.append(f"{(page - 1) * 10 + index}. <@{user_id}> — {value} XP")
        except Exception as error:
            log.warning("Leaderboard read failed guild=%s error=%s", guild_id, type(error).__name__)
            await interaction.followup.send("Не вдалося прочитати рейтинг. Спробуйте пізніше.",
                allowed_mentions=discord.AllowedMentions.none())
            return
        if not current_guild():
            await interaction.followup.send("Сервер більше не доступний. Повторіть команду після підключення.",
                allowed_mentions=discord.AllowedMentions.none())
            return
        label = {"total": "Загальний (чат + голос)", "text": "Чат", "voice": "Голос"}[kind]
        embed = discord.Embed(title=f"Рейтинг · {label} · {page}",
                              description="\n".join(lines) or "Поки немає XP.")
        embed.set_footer(text="По 10 учасників · при однаковому XP менший Discord ID вище")
        await interaction.followup.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    async def checked_target(self, interaction, member, perm):
        guild = permission(interaction, perm)
        if not guild.me or not getattr(guild.me.guild_permissions, perm):
            raise UserError(f"Боту потрібен дозвіл: {perm}.")
        target_check(interaction.user, member, guild.me, guild.owner_id)
        return guild

    @app_commands.command(name="warn", description="Попередити учасника")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warn(self, interaction: discord.Interaction, member: discord.Member,
                   reason: app_commands.Range[str, 1, 500]):
        guild = guild_context(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.moderation.warn(guild.id, interaction.user.id, member.id,
                                                interaction.id, reason)
        except ModerationError as error:
            raise UserError(str(error)) from None
        await moderation_response(interaction, result)

    @app_commands.command(name="warnings", description="Останні 10 чинних попереджень учасника")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        permission(interaction, "moderate_members")
        await interaction.response.defer(ephemeral=True)
        try:
            async with asyncio.timeout(10):
                rows = await self.bot.db.warnings(interaction.guild_id, member.id)
        except Exception:
            raise UserError("Не вдалося прочитати попередження. Перевірте доступність БД.") from None
        embeds = warning_embeds(rows)
        if embeds:
            await interaction.followup.send(embeds=embeds, ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.followup.send("Попереджень немає.", ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="unwarn", description="Відкликати попередження за ID")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def unwarn(self, interaction: discord.Interaction, case_id: str):
        permission(interaction, "moderate_members")
        await interaction.response.defer(ephemeral=True)
        changed = await self.bot.db.revoke_warning(interaction.guild_id, case_id)
        await respond(interaction, "Відкликано." if changed else "Чинного попередження не знайдено.")

    @app_commands.command(name="timeout", description="Тимчасово обмежити учасника")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def timeout(self, interaction: discord.Interaction, member: discord.Member,
                      minutes: app_commands.Range[int, 1, 40320],
                      reason: app_commands.Range[str, 1, 500]):
        guild = await self.checked_target(interaction, member, "moderate_members")
        if member.guild_permissions.administrator:
            raise UserError("Discord не дозволяє timeout адміністратора.")
        await interaction.response.defer(ephemeral=True)
        case = await self.bot.db.create_case(guild.id, member.id, interaction.user.id, "timeout", reason)
        try:
            await member.timeout(datetime.timedelta(minutes=minutes),
                                 reason=f"{interaction.user.id}: {reason}"[:512])
        except discord.HTTPException:
            await self.bot.db.finish_case(guild.id, case, "failed")
            raise
        await self.bot.db.finish_case(guild.id, case, "applied")
        await respond(interaction, f"Timeout: {minutes} хв. ID: {case}")

    @app_commands.command(name="untimeout", description="Зняти тимчасове обмеження")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member):
        guild = await self.checked_target(interaction, member, "moderate_members")
        await interaction.response.defer(ephemeral=True)
        case = await self.bot.db.create_case(guild.id, member.id, interaction.user.id,
                                             "untimeout", "Manual removal")
        try:
            await member.timeout(None, reason=f"{interaction.user.id}: manual removal")
        except discord.HTTPException:
            await self.bot.db.finish_case(guild.id, case, "failed")
            raise
        await self.bot.db.finish_case(guild.id, case, "applied")
        await respond(interaction, "Обмеження знято.")

    @app_commands.command(name="remind", description="Нагадування в поточному каналі")
    @app_commands.guild_only()
    async def remind(self, interaction: discord.Interaction,
                     minutes: app_commands.Range[int, 1, 525600],
                     message: app_commands.Range[str, 1, 1000]):
        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            raise UserError("Виберіть текстовий канал або гілку.")
        if not interaction.channel.permissions_for(interaction.user).view_channel:
            raise UserError("Немає доступу до каналу.")
        await interaction.response.defer(ephemeral=True)
        try:
            job = await self.bot.db.schedule(interaction.guild_id, interaction.user.id,
                interaction.channel_id, message, time.time() + minutes * 60)
        except ValueError as error:
            raise UserError(str(error)) from error
        await respond(interaction, f"Нагадування збережено. ID: {job}")

    @app_commands.command(name="reminders", description="Ваші активні нагадування")
    @app_commands.guild_only()
    async def reminders(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rows = await self.bot.db.reminders(interaction.guild_id, interaction.user.id)
        lines = [f'{r["id"]} · <t:{int(r["due"])}:R>' for r in rows]
        await respond(interaction, "\n".join(lines) or "Активних нагадувань немає.")

    @app_commands.command(name="cancel-reminder", description="Скасувати своє нагадування за ID")
    @app_commands.guild_only()
    async def cancel_reminder(self, interaction: discord.Interaction, reminder_id: str):
        await interaction.response.defer(ephemeral=True)
        changed = await self.bot.db.cancel_reminder(interaction.guild_id, interaction.user.id, reminder_id)
        await respond(interaction, "Скасовано." if changed else "Не знайдено або вже доставляється.")
