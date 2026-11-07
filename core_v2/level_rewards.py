"""Persistent, cumulative XP role rewards with bounded best-effort delivery."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
from math import isqrt

import discord
from discord import app_commands
from discord.ext import commands

from .discord_moderation import DiscordGateway
from .moderation import ModerationError
from .security import DANGEROUS, UserError, guild_context, safe_role

log = logging.getLogger(__name__)
MAX_ACTIVE = 32
REQUEST_TIMEOUT = 20
KINDS = [app_commands.Choice(name="Чат", value="text"), app_commands.Choice(name="Голос", value="voice")]


async def private(interaction, message):
    sender = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    await sender(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def earned_level(value, kind):
    if type(value) is not int or not 0 <= value < 2**63 or kind not in {"text", "voice"}:
        raise ValueError("Invalid XP counter")
    return isqrt(value // (100 if kind == "text" else 50)) + 1


@dataclass
class Snapshot:
    guild: discord.Guild
    member: discord.Member
    bot: discord.Member
    channels: list


class LevelRewards(commands.Cog):
    level_reward = app_commands.Group(name="level-reward", description="Ролі за рівні чату й голосу",
        guild_only=True, default_permissions=discord.Permissions(manage_guild=True, manage_roles=True))

    def __init__(self, bot):
        self.bot = bot
        self.active = set()
        self.tasks = set()
        self.started = set()
        self.closing = False

    def _live(self, guild):
        if self.closing or guild is None or guild.unavailable or self.bot.get_guild(guild.id) is not guild:
            raise UserError("Сервер або модуль нагород зараз недоступний.")

    def _scope(self, interaction):
        guild = guild_context(interaction)
        self._live(guild)
        if interaction.guild_id != guild.id or interaction.user.guild.id != guild.id:
            raise UserError("Учасник має належати цьому серверу.")
        return guild

    def _reserve(self, guild):
        self._live(guild)
        if guild.id in self.active or len(self.active) >= MAX_ACTIVE:
            raise UserError("Модуль нагород зайнятий. Спробуйте за кілька секунд.")
        self.active.add(guild.id)

    @asynccontextmanager
    async def _request(self, guild, *, reserved=False):
        if not reserved:
            self._reserve(guild)
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            self._live(guild)
            async with asyncio.timeout(REQUEST_TIMEOUT):
                yield
        finally:
            self.active.discard(guild.id)
            self.tasks.discard(task)

    async def cog_unload(self):
        self.closing = True
        tasks = tuple(task for task in self.tasks if task is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.active.clear()

    def enqueue(self, guild_id, user_id):
        """Admit before scheduling: no waiter queue, unbounded tasks or SQL retry."""
        if any(type(value) is not int or not 0 < value < 2**63 for value in (guild_id, user_id)):
            return False
        guild = self.bot.get_guild(guild_id)
        try:
            self._reserve(guild)
        except UserError:
            return False
        coroutine = self._automatic(guild, user_id)
        try:
            task = asyncio.create_task(coroutine)
        except BaseException:
            coroutine.close()
            self.active.discard(guild_id)
            raise
        self.tasks.add(task)
        # A task cancelled before its first instruction never enters _request.
        def finished(done):
            self.tasks.discard(done)
            if done not in self.started:
                self.active.discard(guild_id)
            self.started.discard(done)
        task.add_done_callback(finished)
        return True

    async def _automatic(self, guild, user_id):
        self.started.add(asyncio.current_task())
        try:
            async with self._request(guild, reserved=True):
                await self._grant(guild, user_id)
        except Exception as error:
            log.warning("Level reward deferred guild=%s error=%s", guild.id, type(error).__name__)

    async def _snapshot(self, original, user_id, *, admin=False, channels=True):
        self._live(original)
        if self.bot.user is None:
            raise UserError("Discord-клієнт ще не готовий.")
        guild = await self.bot.fetch_guild(original.id, with_counts=False)
        if guild.id != original.id:
            raise UserError("Не вдалося підтвердити сервер.")
        roles = await guild.fetch_roles()
        if any(role.guild.id != guild.id for role in roles) or guild.id not in {r.id for r in roles}:
            raise UserError("Не вдалося підтвердити ролі сервера.")
        guild._roles = {role.id: role for role in roles}
        member = await guild.fetch_member(user_id)
        me = await guild.fetch_member(self.bot.user.id)
        try:
            DiscordGateway._member(guild, member, user_id)
            DiscordGateway._member(guild, me, self.bot.user.id)
        except ModerationError:
            raise UserError("Ролі учасника змінилися. Спробуйте ще раз.") from None
        if member.bot or member.pending:
            raise UserError("Нагороди доступні людям після перевірки учасника сервера.")
        if admin and not (member.guild_permissions.manage_guild and member.guild_permissions.manage_roles):
            raise UserError("Потрібні дозволи Manage Guild і Manage Roles.")
        rooms = await guild.fetch_channels() if channels else []
        if any(room.guild.id != guild.id for room in rooms):
            raise UserError("Не вдалося підтвердити канали сервера.")
        self._live(original)
        return Snapshot(guild, member, me, rooms)

    @staticmethod
    def _role(snapshot, role_id, *, admin=False):
        role = snapshot.guild.get_role(role_id)
        if role is None or not snapshot.bot.guild_permissions.manage_roles:
            raise UserError("Роль видалено або боту бракує Manage Roles.")
        safe_role(role, snapshot.bot, snapshot.member if admin else None, snapshot.guild.owner_id)
        for room in snapshot.channels:
            allow, _ = room.overwrites_for(role).pair()
            if any(getattr(allow, name, False) for name in DANGEROUS):
                raise UserError("Роль має привілейовані дозволи в одному з каналів.")
        return role

    async def _grant(self, original, user_id):
        if not (await self.bot.db.get_settings(original.id))["levels"]:
            raise UserError("Система рівнів вимкнена на цьому сервері.")
        rows = await self.bot.db.level_rewards(original.id)
        if not rows:
            return 0
        profile = await self.bot.db.rank(original.id, user_id)
        earned = {kind: earned_level(profile[f"{kind}_xp"], kind) for kind in ("text", "voice")}
        candidates = list(dict.fromkeys(row["role_id"] for row in rows if row["level"] <= earned[row["kind"]]))
        if not candidates:
            return 0
        snapshot = await self._snapshot(original, user_id)
        # Validate every candidate before the first side effect. Explicit atomic
        # additions preserve unrelated roles and are safe to reconcile later.
        roles = [self._role(snapshot, role_id) for role_id in candidates]
        missing = [role for role in roles if role not in snapshot.member.roles]
        if not (await self.bot.db.get_settings(original.id))["levels"]:
            raise UserError("Система рівнів вимкнена на цьому сервері.")
        count = 0
        for role in missing:
            if not (await self.bot.db.get_settings(original.id))["levels"]:
                raise UserError("Система рівнів вимкнена; подальшу видачу нагород зупинено.")
            self._live(original)
            await snapshot.member.add_roles(role, atomic=True, reason="YEBOYBot: нагорода за досягнутий рівень")
            count += 1
        return count

    @level_reward.command(name="set", description="Призначити безпечну роль за рівень; до 25 правил на сервер")
    @app_commands.choices(kind=KINDS)
    async def set_reward(self, interaction: discord.Interaction, kind: app_commands.Choice[str],
                         level: app_commands.Range[int, 2, 1000], role: discord.Role):
        guild = self._scope(interaction)
        if role.guild.id != guild.id:
            raise UserError("Роль має належати цьому серверу.")
        await interaction.response.defer(ephemeral=True)
        async with self._request(guild):
            snapshot = await self._snapshot(guild, interaction.user.id, admin=True)
            self._role(snapshot, role.id, admin=True)
            self._live(guild)
            try:
                await self.bot.db.set_level_reward(guild.id, kind.value, level, role.id)
            except ValueError:
                raise UserError("Перевірте тип і рівень 2–1000. Ліміт — 25 правил; зайві видаліть через /level-reward remove.") from None
            await private(interaction, "Нагороду збережено. Учасники отримають роль після наступного XP або /claim-rewards.")

    @level_reward.command(name="remove", description="Видалити правило нагороди; вже видані ролі залишаються")
    @app_commands.choices(kind=KINDS)
    async def remove_reward(self, interaction: discord.Interaction, kind: app_commands.Choice[str],
                            level: app_commands.Range[int, 2, 1000]):
        guild = self._scope(interaction)
        await interaction.response.defer(ephemeral=True)
        async with self._request(guild):
            await self._snapshot(guild, interaction.user.id, admin=True, channels=False)
            changed = await self.bot.db.delete_level_reward(guild.id, kind.value, level)
            await private(interaction, "Правило видалено. Вже видані ролі залишаються." if changed else "Правило не знайдено.")

    @level_reward.command(name="list", description="Показати правила нагород цього сервера")
    async def list_rewards(self, interaction: discord.Interaction):
        guild = self._scope(interaction)
        await interaction.response.defer(ephemeral=True)
        async with self._request(guild):
            await self._snapshot(guild, interaction.user.id, admin=True, channels=False)
            rows = await self.bot.db.level_rewards(guild.id)
            lines = [f"{'Чат' if row['kind'] == 'text' else 'Голос'} · рівень {row['level']} → `{row['role_id']}`" for row in rows]
            await private(interaction, "\n".join(lines) if lines else "Нагороди ще не налаштовано.")

    @app_commands.command(name="claim-rewards", description="Отримати свої налаштовані ролі за досягнуті рівні")
    @app_commands.guild_only()
    async def claim_rewards(self, interaction: discord.Interaction):
        guild = self._scope(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            async with self._request(guild):
                count = await self._grant(guild, interaction.user.id)
                await private(interaction, f"Додано ролей: {count}." if count else "Нових доступних нагород немає; вже видані ролі збережено.")
        except (discord.HTTPException, TimeoutError):
            await private(interaction, "Discord не підтвердив усі нагороди. Перевірте ролі та повторіть /claim-rewards пізніше.")
