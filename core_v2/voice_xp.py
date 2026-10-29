"""Conservative, measured voice participation; Discord has no speech detector.

Only a whole continuously observed eligible minute earns 5 XP. Presence in a
voice channel is observable; actual speech and audio are never inspected.
"""
import asyncio
from dataclasses import dataclass
import logging
import time

import discord
from discord.ext import commands, tasks

log = logging.getLogger(__name__)
INTERVAL = 60
MAX_POLL_GAP = 30
MAX_SESSIONS = 25000
MAX_AWARDS = 128


@dataclass
class Session:
    channel_id: int
    since: float


def eligible_members(guild):
    """Return normal-channel humans who can both speak and hear each other."""
    if guild.unavailable:
        return {}
    result = {}
    afk = guild.afk_channel
    for channel in guild.voice_channels:
        if channel.type != discord.ChannelType.voice or (afk and channel.id == afk.id):
            continue
        humans = []
        for member in channel.members:
            state = member.voice
            if (member.bot or state is None or state.channel is None or
                    state.channel.id != channel.id or any(getattr(state, name, False)
                    for name in ("mute", "deaf", "self_mute", "self_deaf", "suppress"))):
                continue
            humans.append(member.id)
        if len(humans) >= 2:
            result.update((user_id, channel.id) for user_id in humans)
    return result


class VoiceXP(commands.Cog):
    def __init__(self, bot, *, clock=time.monotonic):
        self.bot = bot
        self.clock = clock
        self.sessions = {}
        self.enabled_guilds = set()
        self.last_poll = None
        self.gateway_paused = False
        self.paused_shards = set()

    async def cog_load(self):
        if self.bot.is_ready():
            self.collect.start()

    async def cog_unload(self):
        self.collect.cancel()
        task = self.collect.get_task()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        self.reset()

    def reset(self):
        self.sessions.clear()
        self.enabled_guilds.clear()
        self.last_poll = None

    def reset_guild(self, guild_id):
        self.enabled_guilds.discard(guild_id)
        for key in list(self.sessions):
            if key[0] == guild_id:
                del self.sessions[key]

    def reconcile(self, guild, now):
        current = eligible_members(guild)
        for key, session in list(self.sessions.items()):
            if key[0] == guild.id and current.get(key[1]) != session.channel_id:
                del self.sessions[key]
        for user_id, channel_id in current.items():
            key = (guild.id, user_id)
            if key not in self.sessions and len(self.sessions) < MAX_SESSIONS:
                self.sessions[key] = Session(channel_id, now)

    async def poll(self):
        """Bound one collection pass, with no backlog awards or blind SQL retry."""
        if not self.bot.is_ready() or self.gateway_paused:
            self.reset()
            return
        now = self.clock()
        if self.last_poll is not None and (now < self.last_poll or now - self.last_poll > MAX_POLL_GAP):
            self.sessions.clear()
        self.last_poll = now
        connected = {guild.id: guild for guild in self.bot.guilds
                     if not guild.unavailable and guild.shard_id not in self.paused_shards}
        for guild_id in self.enabled_guilds - connected.keys():
            self.reset_guild(guild_id)
        async with asyncio.timeout(15):
            for guild in connected.values():
                try:
                    async with asyncio.timeout(3):
                        enabled = (await self.bot.guild_settings(guild.id))["levels"]
                except Exception as error:
                    self.reset_guild(guild.id)
                    log.warning("Voice XP settings unavailable guild=%s error=%s", guild.id, type(error).__name__)
                    continue
                # Gateway events may run while configuration SQL is awaited.
                # Never restore timers that a disconnect/removal just cleared.
                if not self.bot.is_ready() or self.gateway_paused:
                    self.reset()
                    return
                if (guild.unavailable or guild.shard_id in self.paused_shards or
                        guild not in self.bot.guilds):
                    self.reset_guild(guild.id)
                    continue
                if not enabled:
                    self.reset_guild(guild.id)
                    continue
                self.enabled_guilds.add(guild.id)
                self.reconcile(guild, now)
            due = sorted((session.since, key) for key, session in self.sessions.items()
                         if now - session.since >= INTERVAL)[:MAX_AWARDS]
            semaphore = asyncio.Semaphore(4)

            async def award(key):
                async with semaphore:
                    # A queued member may have left or lost eligibility during
                    # another SQL call. Never recreate a removed session here.
                    session = self.sessions.get(key)
                    if session is None or now - session.since < INTERVAL:
                        return
                    session.since = now
                    try:
                        async with asyncio.timeout(5):
                            await self.bot.db.award(*key, kind="voice")
                    except Exception as error:
                        # A timeout may hide a committed write. Wait a complete
                        # new minute; durable last_voice also rejects duplicates.
                        log.warning("Voice XP update unavailable guild=%s error=%s", key[0], type(error).__name__)

            await asyncio.gather(*(award(key) for _, key in due))

    @tasks.loop(seconds=10)
    async def collect(self):
        try:
            await self.poll()
        except Exception as error:
            self.reset()
            log.warning("Voice XP collection interrupted error=%s", type(error).__name__)

    @collect.before_loop
    async def before_collect(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        self.reset()
        self.gateway_paused = False
        self.paused_shards.clear()
        if not self.collect.is_running():
            self.collect.start()

    @commands.Cog.listener()
    async def on_disconnect(self):
        self.reset()
        self.gateway_paused = True

    @commands.Cog.listener()
    async def on_resumed(self):
        self.reset()
        self.gateway_paused = False

    @commands.Cog.listener()
    async def on_shard_disconnect(self, shard_id):
        self.paused_shards.add(shard_id)
        for guild in self.bot.guilds:
            if guild.shard_id == shard_id:
                self.reset_guild(guild.id)

    @commands.Cog.listener()
    async def on_shard_ready(self, shard_id):
        self.paused_shards.discard(shard_id)
        for guild in self.bot.guilds:
            if guild.shard_id == shard_id:
                self.reset_guild(guild.id)

    @commands.Cog.listener()
    async def on_shard_resumed(self, shard_id):
        await self.on_shard_ready(shard_id)

    @commands.Cog.listener()
    async def on_guild_unavailable(self, guild):
        self.reset_guild(guild.id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        self.reset_guild(guild.id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # discord.py updates its voice cache before dispatching this listener.
        if (not self.bot.is_ready() or self.gateway_paused or
                member.guild.shard_id in self.paused_shards or member.guild.id not in self.enabled_guilds):
            self.reset_guild(member.guild.id)
            return
        self.reconcile(member.guild, self.clock())
