"""Private, read-only help and module discovery for the connected Core runtime."""
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .help_catalog import (CATEGORY_LABELS, collect_commands, page_count, render_help)
from .module_status import collect_module_status
from .security import UserError, guild_context

log = logging.getLogger(__name__)
HELP_TIMEOUT = 120
EDIT_TIMEOUT = 5
MAX_HELP_VIEWS = 100
MENTIONS = discord.AllowedMentions.none()


async def private_message(interaction, message):
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True, allowed_mentions=MENTIONS)
    else:
        await interaction.response.send_message(message, ephemeral=True, allowed_mentions=MENTIONS)


class HelpView(discord.ui.View):
    def __init__(self, owner, user_id, guild_id, *, category="general", page=1):
        super().__init__(timeout=HELP_TIMEOUT)
        self.owner = owner
        self.user_id, self.guild_id = user_id, guild_id
        self.category, self.page = category, page
        self.message = None
        self.expired = False
        self._edits = asyncio.Lock()
        self._refresh()

    def _refresh(self):
        self.entries = collect_commands(self.owner.bot.tree, self.guild_id)
        self.page = min(max(self.page, 1), page_count(self.entries, self.category))
        self.topic.options = [discord.SelectOption(
            label=label, value=key, default=key == self.category,
            description=f"Підключено команд: {sum(entry.category == key for entry in self.entries)}")
            for key, label in CATEGORY_LABELS.items()]
        self.previous.disabled = self.page <= 1
        self.next.disabled = self.page >= page_count(self.entries, self.category)

    def embed(self):
        return render_help(self.entries, self.category, self.page)

    async def interaction_check(self, interaction):
        if (getattr(interaction, "guild_id", None) != self.guild_id
                or interaction.user.id != self.user_id):
            await private_message(interaction, "Ця панель належить автору запиту на цьому сервері. Відкрийте власну /help.")
            return False
        if self.expired or self.owner.closing:
            await private_message(interaction, "Термін панелі минув. Відкрийте /help знову.")
            return False
        return True

    async def update(self, interaction, *, category=None, step=0):
        if not await self.interaction_check(interaction):
            return
        if category is not None and category not in CATEGORY_LABELS:
            await private_message(interaction, "Розділ недоступний. Відкрийте /help знову.")
            return
        await interaction.response.defer()
        try:
            async with asyncio.timeout(EDIT_TIMEOUT):
                async with self._edits:
                    if self.expired or self.owner.closing:
                        await private_message(interaction, "Термін панелі минув. Відкрийте /help знову.")
                        return
                    if category is not None:
                        self.category, self.page = category, 1
                    else:
                        self.page += step
                    self._refresh()
                    await interaction.edit_original_response(embed=self.embed(), view=self,
                                                             allowed_mentions=MENTIONS)
        except (discord.HTTPException, TimeoutError):
            await private_message(interaction, "Не вдалося оновити довідку. Повторіть /help.")

    @discord.ui.select(placeholder="Оберіть розділ довідки", min_values=1, max_values=1, row=0)
    async def topic(self, interaction: discord.Interaction, select: discord.ui.Select):
        await self.update(interaction, category=select.values[0] if select.values else "")

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update(interaction, step=-1)

    @discord.ui.button(label="Далі", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update(interaction, step=1)

    def close_locally(self):
        self.expired = True
        for child in self.children:
            child.disabled = True
        self.stop()
        self.owner.views.discard(self)

    async def expire(self):
        self.close_locally()
        if self.message is not None:
            try:
                async with asyncio.timeout(EDIT_TIMEOUT):
                    async with self._edits:
                        await self.message.edit(view=self, allowed_mentions=MENTIONS)
            except Exception as error:
                log.info("Help panel edit unavailable type=%s", type(error).__name__)

    async def on_timeout(self):
        await self.expire()

    async def on_error(self, interaction, error, item):
        log.warning("Help panel failure type=%s", type(error).__name__)
        try:
            await private_message(interaction, "Не вдалося відкрити розділ. Повторіть /help.")
        except discord.HTTPException:
            log.info("Help panel error reply unavailable")


class Guide(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.views = set()
        self.closing = False

    async def cog_unload(self):
        self.closing = True
        await asyncio.gather(*(view.expire() for view in tuple(self.views)), return_exceptions=True)

    @app_commands.command(name="help", description="Команди бота, приклади та підказки щодо дозволів")
    @app_commands.guild_only()
    @app_commands.choices(topic=[app_commands.Choice(name=label, value=key)
                                for key, label in CATEGORY_LABELS.items()])
    @app_commands.describe(topic="Розділ довідки", page="Сторінка розділу")
    async def help(self, interaction: discord.Interaction,
                   topic: app_commands.Choice[str] | None = None,
                   page: app_commands.Range[int, 1, 10] = 1):
        guild = guild_context(interaction)
        if self.closing:
            raise UserError("Довідка оновлюється. Повторіть запит пізніше.")
        category = topic.value if topic is not None else "general"
        if category not in CATEGORY_LABELS or type(page) is not int or not 1 <= page <= 10:
            raise UserError("Оберіть доступний розділ і сторінку довідки.")
        await interaction.response.defer(ephemeral=True)
        if self.closing:
            raise UserError("Довідка оновлюється. Повторіть запит пізніше.")
        entries = collect_commands(self.bot.tree, guild.id)
        maximum = page_count(entries, category)
        if page > maximum:
            await private_message(interaction, f"У цьому розділі сторінок: {maximum}. Виберіть сторінку від 1 до {maximum}.")
            return
        embed = render_help(entries, category, page)
        view = None
        if len(self.views) < MAX_HELP_VIEWS:
            view = HelpView(self, interaction.user.id, guild.id, category=category, page=page)
            self.views.add(view)
        try:
            message = await interaction.followup.send(embed=embed, view=view, ephemeral=True,
                                                       allowed_mentions=MENTIONS, wait=True)
            if view is not None:
                view.message = message
                if view.expired or self.closing:
                    await view.expire()
        except BaseException:
            if view is not None:
                view.close_locally()
            raise

    @app_commands.command(name="modules", description="Стан модулів і підказки щодо налаштування цього сервера")
    @app_commands.guild_only()
    async def modules(self, interaction: discord.Interaction):
        guild = guild_context(interaction)
        if self.closing:
            raise UserError("Стан модулів оновлюється. Повторіть запит пізніше.")
        await interaction.response.defer(ephemeral=True)
        embed = await collect_module_status(self.bot, guild)
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=MENTIONS)
