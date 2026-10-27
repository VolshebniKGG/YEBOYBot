import asyncio
import datetime
import math
import time

import discord
from discord import app_commands
from discord.ext import commands

from .security import UserError, permission, target_check, respond
from .security import guild_context
from .moderation import ModerationService, ModerationError, valid_id
from .discord_moderation import DiscordGateway, history_embed, warning_embeds
from .clear_commands import preview as clear_preview, status as clear_status


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

    @app_commands.command(name="levels", description="Увімкнути або вимкнути текстовий XP")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def levels(self, interaction: discord.Interaction, enabled: bool):
        guild = permission(interaction, "manage_guild")
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.configure(guild.id, levels=enabled)
        self.bot.settings_cache.pop(guild.id, None)
        await respond(interaction, f"Текстовий XP: {'увімкнено' if enabled else 'вимкнено'}.")

    @app_commands.command(name="rank", description="Рівень учасника на цьому сервері")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None):
        await interaction.response.defer()
        member = member or interaction.user
        row = await self.bot.db.rank(interaction.guild_id, member.id)
        total = row["text_xp"] + row["voice_xp"]
        embed = discord.Embed(title=f"Ранг: {member.display_name}",
                              description=f"Рівень {math.isqrt(total // 100) + 1} · {total} XP")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="leaderboard", description="Рейтинг цього сервера")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction,
                          page: app_commands.Range[int, 1, 1000] = 1):
        await interaction.response.defer()
        rows = await self.bot.db.leaderboard(interaction.guild_id, page)
        lines = [f'{(page-1)*10+i}. <@{r["user_id"]}> — {r["text_xp"]+r["voice_xp"]} XP'
                 for i, r in enumerate(rows, 1)]
        await interaction.followup.send(embed=discord.Embed(
            title=f"Рейтинг · {page}", description="\n".join(lines) or "Поки немає XP."))

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
