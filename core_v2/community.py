"""Opt-in member onboarding and metadata-only community event logs."""
import asyncio
import logging
from contextlib import asynccontextmanager

import discord
from discord import app_commands
from discord.ext import commands

from .security import UserError, permission, safe_role

log = logging.getLogger(__name__)
TIMEOUT = 15
MAX_EVENTS = 16


def validate_channel(guild, channel):
    if channel.guild.id != guild.id:
        raise UserError("Канал має належати цьому серверу.")
    me = guild.me
    if me is None:
        raise UserError("Не вдалося підтвердити права бота.")
    permissions = channel.permissions_for(me)
    if not permissions.view_channel or not permissions.send_messages:
        raise UserError("Боту потрібні View Channel і Send Messages у вибраному каналі.")


def validate_autorole(guild, role, actor=None):
    me = guild.me
    if role.guild.id != guild.id or me is None or not me.guild_permissions.manage_roles:
        raise UserError("Роль має належати серверу; боту потрібен дозвіл Manage Roles.")
    safe_role(role, me, actor, guild.owner_id)


class Community(commands.Cog):
    community = app_commands.Group(name="community", description="Привітання, автороль і журнал подій",
                                   guild_only=True, default_permissions=discord.Permissions(manage_guild=True))

    def __init__(self, bot):
        self.bot = bot
        self._active = 0

    @asynccontextmanager
    async def _slot(self):
        # Drop excess transient events instead of accumulating unbounded waiters
        # during a raid. These best-effort notifications are not an audit ledger.
        admitted = self._active < MAX_EVENTS
        if admitted:
            self._active += 1
        try:
            yield admitted
        finally:
            if admitted:
                self._active -= 1

    async def _configure(self, interaction, key, value):
        async with asyncio.timeout(TIMEOUT):
            await self.bot.db.configure(interaction.guild.id, **{key: value})
        self.bot.settings_cache.pop(interaction.guild.id, None)
        await interaction.followup.send("Налаштування збережено.", ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())

    def _members_enabled(self):
        if not self.bot.intents.members:
            raise UserError("Увімкніть ENABLE_MEMBERS=true та Server Members Intent у Discord Developer Portal, потім перезапустіть бота.")

    @community.command(name="welcome", description="Увімкнути привітання; без каналу — вимкнути")
    async def welcome(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        guild = permission(interaction, "manage_guild")
        if channel is not None:
            self._members_enabled()
            validate_channel(guild, channel)
        await interaction.response.defer(ephemeral=True)
        await self._configure(interaction, "welcome_channel", channel.id if channel else None)

    @community.command(name="autorole", description="Призначити безпечну роль новачкам; без ролі — вимкнути")
    async def autorole(self, interaction: discord.Interaction, role: discord.Role | None = None):
        guild = permission(interaction, "manage_guild")
        permission(interaction, "manage_roles")
        if role is not None:
            self._members_enabled()
            validate_autorole(guild, role, interaction.user)
        await interaction.response.defer(ephemeral=True)
        await self._configure(interaction, "autorole", role.id if role else None)

    @community.command(name="logs", description="Журнал входів, виходів, створення/видалення ролей і каналів")
    async def logs(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        guild = permission(interaction, "manage_guild")
        if channel is not None:
            validate_channel(guild, channel)
        await interaction.response.defer(ephemeral=True)
        await self._configure(interaction, "log_channel", channel.id if channel else None)
        if channel is not None and not self.bot.intents.members:
            await interaction.followup.send("Входи/виходи учасників потребують Server Members Intent та ENABLE_MEMBERS=true. Події ролей і каналів уже доступні.", ephemeral=True)

    async def _settings(self, guild):
        if guild.unavailable or self.bot.get_guild(guild.id) is not guild:
            return None
        try:
            async with asyncio.timeout(TIMEOUT):
                return await self.bot.db.get_settings(guild.id)
        except Exception:
            log.warning("Community settings unavailable")
            return None

    async def _send(self, guild, channel_id, content):
        if not channel_id or guild.unavailable or self.bot.get_guild(guild.id) is not guild:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            validate_channel(guild, channel)
            async with asyncio.timeout(TIMEOUT):
                await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
        except (UserError, discord.HTTPException, TimeoutError):
            log.warning("Community message unavailable")

    async def _assign(self, member, settings):
        if member.bot or member.pending or not settings.get("autorole"):
            return
        role = member.guild.get_role(settings["autorole"])
        if role is None or role in member.roles:
            return
        try:
            # Roles can acquire dangerous permissions or move above the bot after setup.
            validate_autorole(member.guild, role)
            async with asyncio.timeout(TIMEOUT):
                await member.add_roles(role, reason="YEBOYBot: налаштована автороль", atomic=True)
        except (UserError, discord.HTTPException, TimeoutError):
            log.warning("Community autorole unavailable")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        async with self._slot() as admitted:
            if not admitted:
                return
            settings = await self._settings(member.guild)
            if settings is None:
                return
            await self._assign(member, settings)
            await self._send(member.guild, settings.get("welcome_channel"), f"Вітаємо на сервері, <@{member.id}>!")
            await self._send(member.guild, settings.get("log_channel"), f"Учасник приєднався. ID: {member.id}")

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        # Never bypass Discord Membership Screening by assigning a role early.
        if before.pending and not after.pending:
            async with self._slot() as admitted:
                if not admitted:
                    return
                settings = await self._settings(after.guild)
                if settings is not None:
                    await self._assign(after, settings)

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload):
        guild = self.bot.get_guild(payload.guild_id)
        if guild is not None:
            await self._event(guild, f"Учасник залишив сервер. ID: {payload.user.id}")

    async def _event(self, guild, text):
        async with self._slot() as admitted:
            if not admitted:
                return
            settings = await self._settings(guild)
            if settings is not None:
                await self._send(guild, settings.get("log_channel"), text)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        await self._event(channel.guild, f"Створено канал. ID: {channel.id}")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await self._event(channel.guild, f"Видалено канал. ID: {channel.id}")

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        await self._event(role.guild, f"Створено роль. ID: {role.id}")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        await self._event(role.guild, f"Видалено роль. ID: {role.id}")
