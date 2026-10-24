import asyncio
import logging
import time
from collections import OrderedDict

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .db import Database
from .security import UserError

log = logging.getLogger(__name__)


class Tree(app_commands.CommandTree):
    async def interaction_check(self, interaction):
        if interaction.guild_id is None:
            raise UserError("Команда доступна лише на сервері.")
        key = (interaction.guild_id, interaction.user.id)
        now = time.monotonic()
        previous = self.client.command_times.get(key, 0)
        if now - previous < 1:
            raise UserError("Зачекайте секунду між командами.")
        self.client.command_times[key] = now
        self.client.command_times.move_to_end(key)
        if len(self.client.command_times) > 20000:
            self.client.command_times.popitem(last=False)
        return True

    async def on_error(self, interaction, error):
        original = getattr(error, "original", error)
        if isinstance(original, UserError):
            message = str(original)
        elif isinstance(original, (app_commands.CheckFailure, discord.Forbidden)):
            message = "Недостатньо дозволів. Перевірте права та порядок ролей."
        elif isinstance(original, discord.NotFound):
            message = "Учасник або канал більше не доступний."
        else:
            log.error("Command failure", exc_info=(type(original), original, original.__traceback__))
            message = "Не вдалося виконати команду. Подробиці записано в журнал."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            log.warning("Cannot deliver interaction error")


class Bot(commands.AutoShardedBot):
    def __init__(self, settings):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.members = settings.members
        super().__init__(command_prefix=commands.when_mentioned, help_command=None,
                         intents=intents, tree_cls=Tree, shard_count=settings.shard_count,
                         max_messages=100, chunk_guilds_at_startup=False,
                         member_cache_flags=discord.MemberCacheFlags.none(),
                         allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.db = Database(settings.database)
        self.command_times = OrderedDict()
        self.settings_cache = OrderedDict()
        self.xp_times = OrderedDict()

    async def guild_settings(self, guild_id):
        now = time.monotonic()
        cached = self.settings_cache.get(guild_id)
        if cached and now - cached[0] < 15:
            self.settings_cache.move_to_end(guild_id)
            return cached[1]
        result = await self.db.get_settings(guild_id)
        self.settings_cache[guild_id] = (now, result)
        self.settings_cache.move_to_end(guild_id)
        if len(self.settings_cache) > 10000:
            self.settings_cache.popitem(last=False)
        return result

    async def setup_hook(self):
        await self.db.initialize()
        from .commands import Core
        await self.add_cog(Core(self))
        if self.settings.sync == "test":
            guild = discord.Object(id=self.settings.test_guild)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        elif self.settings.sync == "global":
            await self.tree.sync()
        self.deliver.start()

    async def on_ready(self):
        log.info("Ready guilds=%s shards=%s", len(self.guilds), self.shard_count)

    async def on_message(self, message):
        if not message.guild or message.author.bot or message.webhook_id:
            return
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self.xp_times.get(key, 0) < 60:
            return
        self.xp_times[key] = now
        self.xp_times.move_to_end(key)
        if len(self.xp_times) > 50000:
            self.xp_times.popitem(last=False)
        try:
            if (await self.guild_settings(message.guild.id))["levels"]:
                await self.db.award(*key)
        except Exception:
            log.exception("XP update failed")

    async def send_job(self, job):
        success = permanent = False
        try:
            guild = self.get_guild(job["guild_id"])
            channel = guild.get_channel_or_thread(job["channel_id"]) if guild else None
            if channel is None:
                permanent = True
            else:
                # A reminder must not expose a private-channel message after its
                # author loses access or leaves the guild.
                member = await guild.fetch_member(job["user_id"])
                if not channel.permissions_for(member).view_channel:
                    permanent = True
                else:
                    await channel.send(
                        f'<@{job["user_id"]}> Нагадування: {job["body"]}',
                        allowed_mentions=discord.AllowedMentions(
                            users=[discord.Object(id=job["user_id"])]))
                    success = True
        except (discord.Forbidden, discord.NotFound):
            permanent = True
        except discord.HTTPException:
            log.warning("Reminder delivery failed; will retry")
        await self.db.finish_job(job["guild_id"], job["id"], job["attempts"], success, permanent)

    async def bounded_job(self, job):
        try:
            async with asyncio.timeout(30):
                await self.send_job(job)
        except Exception:
            # The durable lease expires even if a database/network failure
            # prevents recording the retry.
            log.exception("Reminder job failed")

    @tasks.loop(seconds=10)
    async def deliver(self):
        try:
            rows = await self.db.claim_jobs([g.id for g in self.guilds if not g.unavailable])
            await asyncio.gather(*(self.bounded_job(row) for row in rows))
        except Exception:
            log.exception("Reminder scheduler failed; next iteration will retry")

    @deliver.before_loop
    async def before_deliver(self):
        await self.wait_until_ready()

    async def close(self):
        self.deliver.cancel()
        task = self.deliver.get_task()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        try:
            await super().close()
        finally:
            await self.db.close()
