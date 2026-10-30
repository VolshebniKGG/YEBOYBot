"""Persistent, opt-in self-role buttons with fresh authorization per request."""
import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from .discord_moderation import DiscordGateway
from .moderation import ModerationError
from .security import DANGEROUS, UserError, guild_context, safe_role

log = logging.getLogger(__name__)
PANEL_ID = re.compile(r"[0-9a-f]{32}")
MAX_ACTIVE = 32
MAX_COOLDOWNS = 10000
REQUEST_TIMEOUT = 20


async def private(interaction, text):
    sender = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    await sender(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@dataclass
class Snapshot:
    guild: discord.Guild
    actor: discord.Member
    bot: discord.Member
    channel: discord.TextChannel | None
    role: discord.Role | None


class RoleButton(discord.ui.DynamicItem[discord.ui.Button],
                 template=r"yeboy:role:(?P<panel>[0-9a-f]{32}):(?P<action>give|remove)"):
    def __init__(self, panel_id, action):
        if not PANEL_ID.fullmatch(panel_id) or action not in {"give", "remove"}:
            raise ValueError("Invalid role button")
        self.panel_id, self.action = panel_id, action
        super().__init__(discord.ui.Button(
            label="Отримати роль" if action == "give" else "Зняти роль",
            style=discord.ButtonStyle.success if action == "give" else discord.ButtonStyle.secondary,
            custom_id=f"yeboy:role:{panel_id}:{action}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["panel"], match["action"])

    async def callback(self, interaction):
        cog = interaction.client.get_cog("RolePanels")
        if cog is None:
            await private(interaction, "Модуль ролей зараз недоступний.")
            return
        await cog.click(interaction, self.panel_id, self.action)


def panel_view(panel_id):
    view = discord.ui.View(timeout=None)
    view.add_item(RoleButton(panel_id, "give"))
    view.add_item(RoleButton(panel_id, "remove"))
    return view


class RolePanels(commands.Cog):
    role_panel = app_commands.Group(name="role-panel", description="Панелі самостійного вибору ролей",
        guild_only=True, default_permissions=discord.Permissions(manage_guild=True, manage_roles=True))

    def __init__(self, bot, *, clock=time.monotonic):
        self.bot, self.clock = bot, clock
        self.active = set()
        self.cooldowns = OrderedDict()
        self.tasks = set()
        self.closing = False

    async def cog_load(self):
        self.bot.add_dynamic_items(RoleButton)

    async def cog_unload(self):
        self.closing = True
        self.bot.remove_dynamic_items(RoleButton)
        tasks = tuple(task for task in self.tasks if task is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _scope(self, interaction):
        guild = guild_context(interaction)
        if (self.closing or guild.unavailable or self.bot.get_guild(guild.id) is not guild
                or interaction.guild_id != guild.id or interaction.user.guild.id != guild.id):
            raise UserError("Сервер або модуль зараз недоступний.")
        return guild.id

    @asynccontextmanager
    async def _request(self, guild_id):
        if self.closing or guild_id in self.active or len(self.active) >= MAX_ACTIVE:
            raise UserError("Інша операція з ролями ще виконується. Спробуйте за кілька секунд.")
        task = asyncio.current_task()
        self.active.add(guild_id)
        self.tasks.add(task)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                yield
        finally:
            self.active.discard(guild_id)
            self.tasks.discard(task)

    async def _snapshot(self, guild_id, actor_id, *, channel_id=None, role_id=None, admin=False):
        if self.bot.user is None:
            raise UserError("Discord-клієнт ще не готовий.")
        guild = await self.bot.fetch_guild(guild_id, with_counts=False)
        if guild.id != guild_id:
            raise UserError("Не вдалося підтвердити сервер.")
        roles = await guild.fetch_roles()
        if any(role.guild.id != guild_id for role in roles) or guild_id not in {r.id for r in roles}:
            raise UserError("Не вдалося підтвердити ролі сервера.")
        # Only this detached REST guild is updated; shared Gateway caches remain untouched.
        guild._roles = {role.id: role for role in roles}
        actor = await guild.fetch_member(actor_id)
        me = await guild.fetch_member(self.bot.user.id)
        try:
            DiscordGateway._member(guild, actor, actor_id)
            DiscordGateway._member(guild, me, self.bot.user.id)
        except ModerationError:
            raise UserError("Ролі учасника змінилися. Спробуйте ще раз.") from None
        if actor.bot or actor.pending:
            raise UserError("Спочатку завершіть перевірку учасника сервера.")
        if admin and not (actor.guild_permissions.manage_guild and actor.guild_permissions.manage_roles):
            raise UserError("Потрібні дозволи Manage Guild і Manage Roles.")
        channel = None
        if channel_id is not None:
            channel = await guild.fetch_channel(channel_id)
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild_id:
                raise UserError("Панель доступна лише в текстовому каналі цього сервера.")
            if not channel.permissions_for(actor).view_channel or not channel.permissions_for(me).view_channel:
                raise UserError("Немає доступу до каналу панелі.")
            if admin and not (channel.permissions_for(actor).send_messages and channel.permissions_for(me).send_messages):
                raise UserError("Автору й боту потрібен Send Messages у каналі панелі.")
        role = None
        if role_id is not None:
            role = guild.get_role(role_id)
            if role is None or not me.guild_permissions.manage_roles:
                raise UserError("Роль видалено або боту бракує Manage Roles.")
            safe_role(role, me, actor if admin else None, guild.owner_id)
            # A role may have no guild privileges but gain Manage Messages through
            # a channel overwrite. Check current channel overwrites before issuing it.
            channels = await guild.fetch_channels()
            for item in channels:
                if item.guild.id != guild_id:
                    raise UserError("Не вдалося підтвердити канали сервера.")
                allow, _ = item.overwrites_for(role).pair()
                if any(getattr(allow, name, False) for name in DANGEROUS):
                    raise UserError("Роль має привілейовані дозволи в одному з каналів.")
        return Snapshot(guild, actor, me, channel, role)

    @role_panel.command(name="create", description="Опублікувати кнопки отримання й зняття однієї безпечної ролі")
    async def create(self, interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
        guild_id = self._scope(interaction)
        if channel.guild.id != guild_id or role.guild.id != guild_id:
            raise UserError("Канал і роль мають належати цьому серверу.")
        await interaction.response.defer(ephemeral=True)
        async with self._request(guild_id):
            snapshot = await self._snapshot(guild_id, interaction.user.id,
                channel_id=channel.id, role_id=role.id, admin=True)
            try:
                panel_id = await self.bot.db.create_panel(guild_id, role.id)
            except ValueError:
                raise UserError("Не вдалося створити панель. Ліміт — 50 активних панелей; "
                                "перевірте /role-panel list і вимкніть зайві через /role-panel remove.") from None
            try:
                await snapshot.channel.send(
                    f"Самостійний вибір ролі <@&{role.id}>.\nОберіть потрібну дію.\nПанель: `{panel_id}`",
                    view=panel_view(panel_id), allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.NotFound):
                await self.bot.db.delete_panel(guild_id, panel_id)
                raise UserError("Discord відхилив публікацію панелі; запис відкликано.") from None
            except (discord.HTTPException, TimeoutError):
                raise UserError(f"Публікацію не підтверджено. Перевірте канал; ID для відкликання: {panel_id}") from None
            await private(interaction, f"Панель створено. ID: {panel_id}")

    @role_panel.command(name="list", description="Показати активні панелі цього сервера")
    async def list_panels(self, interaction: discord.Interaction, page: app_commands.Range[int, 1, 5] = 1):
        guild_id = self._scope(interaction)
        await interaction.response.defer(ephemeral=True)
        async with self._request(guild_id):
            await self._snapshot(guild_id, interaction.user.id, admin=True)
            rows = await self.bot.db.list_panels(guild_id, page=page)
            text = "\n".join(f"`{row['id']}` → роль `{row['role_id']}`" for row in rows)
            await private(interaction, f"Панелі · сторінка {page}\n{text}" if rows else "На цій сторінці панелей немає.")

    @role_panel.command(name="remove", description="Відкликати панель; вже видані ролі залишаються")
    async def remove(self, interaction: discord.Interaction, panel_id: app_commands.Range[str, 32, 32]):
        guild_id = self._scope(interaction)
        if not PANEL_ID.fullmatch(panel_id):
            raise UserError("Некоректний ID панелі.")
        await interaction.response.defer(ephemeral=True)
        async with self._request(guild_id):
            await self._snapshot(guild_id, interaction.user.id, admin=True)
            changed = await self.bot.db.delete_panel(guild_id, panel_id)
            await private(interaction, "Панель відкликано. Старі кнопки більше не видають ролі; вже видані ролі залишаються."
                          if changed else "Активну панель не знайдено.")

    async def click(self, interaction, panel_id, action):
        try:
            guild_id = self._scope(interaction)
            if not isinstance(panel_id, str) or not PANEL_ID.fullmatch(panel_id) or action not in {"give", "remove"}:
                raise UserError("Некоректна кнопка ролі.")
            message = interaction.message
            if (message is None or self.bot.user is None or message.author.id != self.bot.user.id
                    or message.guild is None or message.guild.id != guild_id
                    or message.channel.id != interaction.channel_id):
                raise UserError("Не вдалося підтвердити повідомлення панелі.")
            key, now = (guild_id, interaction.user.id), self.clock()
            if now - self.cooldowns.get(key, float('-inf')) < 2:
                raise UserError("Зачекайте дві секунди між натисканнями.")
            self.cooldowns[key] = now
            self.cooldowns.move_to_end(key)
            if len(self.cooldowns) > MAX_COOLDOWNS:
                self.cooldowns.popitem(last=False)
            await interaction.response.defer(ephemeral=True)
            async with self._request(guild_id):
                role_id = await self.bot.db.panel(guild_id, panel_id)
                if role_id is None:
                    raise UserError("Панель відкликано або більше не існує.")
                snapshot = await self._snapshot(guild_id, interaction.user.id,
                    channel_id=interaction.channel_id, role_id=role_id)
                present = snapshot.role in snapshot.actor.roles
                if (action == "give") == present:
                    await private(interaction, "Роль уже отримано." if present else "У вас уже немає цієї ролі.")
                    return
                method = snapshot.actor.add_roles if action == "give" else snapshot.actor.remove_roles
                await method(snapshot.role, atomic=True, reason=f"YEBOYBot self-role panel {panel_id}")
                await private(interaction, "Роль отримано." if action == "give" else "Роль знято.")
        except UserError as error:
            await private(interaction, str(error))
        except (discord.HTTPException, TimeoutError):
            await private(interaction, "Discord не підтвердив операцію. Перевірте свої ролі перед повтором.")
        except Exception:
            log.warning("Self-role request failed")
            await private(interaction, "Не вдалося виконати операцію з роллю. Спробуйте пізніше.")
