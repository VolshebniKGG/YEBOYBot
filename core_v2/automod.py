"""Opt-in metadata-only flood protection; no message-content intent required."""
import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from .security import permission

log = logging.getLogger(__name__)
WINDOW = 5.0
MESSAGE_LIMIT = 5
MENTION_LIMIT = 8
MAX_USERS = 20000
MAX_GUILDS = 10000
MAX_INFLIGHT = 20
USER_DELETE_INTERVAL = 1.0
GUILD_DELETE_INTERVAL = 0.25


@dataclass
class Window:
    messages: deque = field(default_factory=lambda: deque(maxlen=MESSAGE_LIMIT + 1))
    last_delete: float = float("-inf")


class AutoMod(commands.Cog):
    def __init__(self, bot, *, clock=time.monotonic):
        self.bot = bot
        self.clock = clock
        self.windows = OrderedDict()
        self.guild_deletes = OrderedDict()
        self.inflight = 0

    @app_commands.command(name="automod", description="Увімкнути або вимкнути захист від флуду та масових згадок")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def automod(self, interaction: discord.Interaction, enabled: bool):
        guild = permission(interaction, "manage_guild")
        await interaction.response.defer(ephemeral=True)
        async with asyncio.timeout(10):
            await self.bot.db.configure(guild.id, automod=enabled)
        self.bot.settings_cache.pop(guild.id, None)
        for key in list(self.windows):
            if key[0] == guild.id:
                del self.windows[key]
        self.guild_deletes.pop(guild.id, None)
        text = ("Автомодерацію увімкнено: понад 5 повідомлень за 5 секунд або 8 різних згадок. "
                "Для видалення боту потрібні View Channel і Manage Messages у кожному каналі. "
                "Повідомлення модераторів не перевіряються." if enabled else "Автомодерацію вимкнено.")
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def inspect_message(self, message):
        """True means detected spam, including when deletion could not be performed.

        Caller must skip XP for True. Only this incoming message can be deleted;
        no history access, content inspection, timeout or ban occurs.
        """
        guild = message.guild
        if (guild is None or guild.unavailable or self.bot.get_guild(guild.id) is not guild
                or message.author.bot or message.webhook_id):
            return False
        author = message.author
        if not isinstance(author, discord.Member):
            return False
        permissions = author.guild_permissions
        if author.id == guild.owner_id or any(getattr(permissions, name, False) for name in (
                "administrator", "manage_guild", "manage_messages", "moderate_members")):
            return False
        try:
            async with asyncio.timeout(5):
                settings = await self.bot.guild_settings(guild.id)
        except Exception:
            log.warning("AutoMod settings unavailable")
            return False
        if settings.get("automod") is not True:
            return False
        if guild.unavailable or self.bot.get_guild(guild.id) is not guild:
            return False
        now = self.clock()
        key = (guild.id, author.id)
        window = self.windows.setdefault(key, Window())
        self.windows.move_to_end(key)
        if len(self.windows) > MAX_USERS:
            self.windows.popitem(last=False)
        while window.messages and window.messages[0] <= now - WINDOW:
            window.messages.popleft()
        window.messages.append(now)
        mentions = {member.id for member in message.mentions}
        mentions.update(role.id for role in message.role_mentions)
        flagged = len(window.messages) > MESSAGE_LIMIT or len(mentions) >= MENTION_LIMIT
        if not flagged:
            return False
        me = guild.me
        if me is None:
            return True
        channel_permissions = message.channel.permissions_for(me)
        if not channel_permissions.view_channel or not channel_permissions.manage_messages:
            return True
        if (self.inflight >= MAX_INFLIGHT or now - window.last_delete < USER_DELETE_INTERVAL
                or now - self.guild_deletes.get(guild.id, float("-inf")) < GUILD_DELETE_INTERVAL):
            return True
        # Reserve before awaiting Discord, so concurrent events cannot bypass limits.
        window.last_delete = now
        self.guild_deletes[guild.id] = now
        self.guild_deletes.move_to_end(guild.id)
        if len(self.guild_deletes) > MAX_GUILDS:
            self.guild_deletes.popitem(last=False)
        self.inflight += 1
        try:
            async with asyncio.timeout(5):
                await message.delete()
        except (discord.HTTPException, TimeoutError):
            log.warning("AutoMod deletion unavailable")
        finally:
            self.inflight -= 1
        return True
