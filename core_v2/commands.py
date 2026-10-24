import datetime
import math
import time

import discord
from discord import app_commands
from discord.ext import commands

from .security import UserError, permission, target_check, respond


class Core(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
        guild = await self.checked_target(interaction, member, "moderate_members")
        await interaction.response.defer(ephemeral=True)
        case = await self.bot.db.create_case(guild.id, member.id, interaction.user.id,
                                             "warn", reason, status="applied")
        await respond(interaction, f"Попередження збережено. ID: {case}")

    @app_commands.command(name="warnings", description="Останні 10 чинних попереджень учасника")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        permission(interaction, "moderate_members")
        await interaction.response.defer(ephemeral=True)
        rows = await self.bot.db.warnings(interaction.guild_id, member.id)
        # Separate embeds keep each field below Discord limits.
        embeds = [discord.Embed(title=r["id"], description=r["reason"]) for r in rows]
        if embeds:
            await interaction.followup.send(embeds=embeds, ephemeral=True)
        else:
            await respond(interaction, "Попереджень немає.")

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
