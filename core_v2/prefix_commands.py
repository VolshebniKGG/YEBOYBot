"""Opt-in ! frontend; a real button interaction preserves private slash replies.

Prefix authorization uses default permissions and runtime checks, independently
of Discord Integrations' per-application-command overrides.
"""
import asyncio
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass
import logging
import re
import shlex
import time
from types import SimpleNamespace

import discord
from discord import app_commands
from discord.ext import commands

from .help_catalog import CATEGORY_LABELS, collect_commands, page_count, render_help
from .module_status import collect_module_status
from .security import UserError

log = logging.getLogger(__name__)
DIRECT_PRIVATE = frozenset({'help', 'modules', 'music queue'})
MENTIONS = discord.AllowedMentions.none()
MAX_PENDING = 256
MAX_PER_GUILD = 16
LIFETIME = 120
ALIASES = {name: ('music', name) for name in
           ('play', 'queue', 'remove', 'skip', 'stop', 'join', 'pause', 'resume', 'volume', 'autoplay')}
ALIASES['top'] = ('leaderboard',)


def resolve_command(tree, words, guild_id):
    if not words:
        raise UserError('Приклад: !help, !play назва пісні або !music queue.')
    words = list(words)
    first = words.pop(0).lower()
    parts = list(ALIASES.get(first, (first,)))
    command = tree.get_command(parts.pop(0), guild=discord.Object(id=guild_id))
    if command is None:
        command = tree.get_command(ALIASES.get(first, (first,))[0])
    words = parts + words
    while isinstance(command, app_commands.Group):
        if not words:
            raise UserError('Укажіть підкоманду; див. /help або !help.')
        command = command.get_command(words.pop(0).lower())
    if not isinstance(command, app_commands.Command):
        raise UserError('Команду не знайдено або модуль вимкнено. Див. /help.')
    return command, words


def parse_arguments(command, words):
    """Parse primitive values without REST; SDK transforms typed objects on click."""
    values = {}
    words = list(words)
    params = command.parameters
    for index, param in enumerate(params):
        if not words:
            if param.required:
                raise UserError(f'Потрібен параметр {param.display_name}. Див. /help.')
            continue
        raw = words.pop(0)
        if raw == '-' and not param.required:
            continue
        kind = param.type
        if kind is discord.AppCommandOptionType.string:
            if index == len(params) - 1 and not param.choices:
                raw = ' '.join([raw, *words])
                words.clear()
            value = raw
            size = len(value)
        elif kind is discord.AppCommandOptionType.integer:
            if not re.fullmatch(r'-?[0-9]+', raw):
                raise UserError(f'{param.display_name}: потрібне ціле число.')
            value = int(raw)
            size = value
        elif kind is discord.AppCommandOptionType.boolean:
            if raw.lower() not in {'true', 'false', 'так', 'ні'}:
                raise UserError(f'{param.display_name}: використайте true або false.')
            value = raw.lower() in {'true', 'так'}
            size = None
        elif kind in {discord.AppCommandOptionType.user, discord.AppCommandOptionType.role,
                      discord.AppCommandOptionType.channel}:
            pattern = {discord.AppCommandOptionType.user: r'(?:<@!?)?([0-9]{1,19})>?',
                       discord.AppCommandOptionType.role: r'(?:<@&)?([0-9]{1,19})>?',
                       discord.AppCommandOptionType.channel: r'(?:<#)?([0-9]{1,19})>?'}[kind]
            match = re.fullmatch(pattern, raw)
            if not match or not 0 < int(match[1]) < 2**63:
                raise UserError(f'{param.display_name}: потрібна згадка або числовий ID.')
            value = int(match[1])
            size = None
        else:
            raise UserError('Цей тип параметра доступний лише через slash-команду.')
        if param.choices:
            choice = next((item for item in param.choices if str(item.value) == str(value)
                           or item.name.casefold() == str(value).casefold()), None)
            if choice is None:
                raise UserError(f'{param.display_name}: допустимо ' + ', '.join(str(c.value) for c in param.choices))
            value = choice.value
        if size is not None and ((param.min_value is not None and size < param.min_value)
                                 or (param.max_value is not None and size > param.max_value)):
            raise UserError(f'{param.display_name}: значення поза дозволеними межами.')
        values[param.display_name] = value
    if words:
        raise UserError('Зайві параметри. Текст із пробілами перед іншими параметрами беріть у лапки.')
    return values


@dataclass
class ChannelValue:
    channel: object

    def resolve(self):
        return self.channel


async def namespace_for(command, values, guild):
    result = dict(values)
    for param in command.parameters:
        if param.display_name not in result:
            continue
        value = result[param.display_name]
        if param.type is discord.AppCommandOptionType.user:
            value = await guild.fetch_member(value)
        elif param.type is discord.AppCommandOptionType.role:
            roles = await guild.fetch_roles()
            value = next((role for role in roles if role.id == value), None)
            if value is None:
                raise UserError('Роль більше не існує.')
        elif param.type is discord.AppCommandOptionType.channel:
            channel = await guild.fetch_channel(value)
            if channel.guild.id != guild.id or (param.channel_types and channel.type not in param.channel_types):
                raise UserError('Канал має належати серверу й відповідати потрібному типу.')
            value = ChannelValue(channel)
        if param.type in {discord.AppCommandOptionType.user, discord.AppCommandOptionType.role}:
            if value.guild.id != guild.id:
                raise UserError('Параметр належить іншому серверу.')
        result[param.display_name] = value
    return SimpleNamespace(**result)


def permission_check(command, permissions):
    node = command
    while node is not None:
        required = node.default_permissions
        if required is not None and not permissions.administrator:
            if required.value == 0 or permissions.value & required.value != required.value:
                raise UserError('Недостатньо дозволів для цієї команди.')
        node = node.parent


async def private(interaction, content):
    sender = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    await sender(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class PrefixView(discord.ui.View):
    def __init__(self, cog, message, command, values):
        super().__init__(timeout=LIFETIME)
        self.cog, self.command, self.values = cog, command, values
        self.guild_id, self.channel_id, self.user_id = message.guild.id, message.channel.id, message.author.id
        self.used = False
        self.created = cog.clock()

    async def on_timeout(self):
        self.used = True
        self.cog.pending.discard(self)
        self.stop()

    @discord.ui.button(label='Виконати команду', style=discord.ButtonStyle.primary)
    async def execute(self, interaction: discord.Interaction, button: discord.ui.Button):
        if (interaction.guild_id != self.guild_id or interaction.channel_id != self.channel_id
                or interaction.user.id != self.user_id):
            await private(interaction, 'Ця кнопка належить автору команди в початковому каналі.')
            return
        if self.used or self.cog.closing or self not in self.cog.pending or self.cog.clock() - self.created >= LIFETIME:
            await private(interaction, 'Команду вже використано або час минув. Введіть її знову.')
            return
        self.used = True  # Before any await: a duplicate click cannot execute twice.
        self.stop()
        task = asyncio.current_task()
        self.cog.tasks.add(task)
        try:
            guild = interaction.guild
            if (guild is None or guild.unavailable or self.cog.bot.get_guild(self.guild_id) is not guild
                    or not isinstance(interaction.user, discord.Member) or interaction.user.bot
                    or interaction.user.pending):
                raise UserError('Учасник або сервер зараз недоступний.')
            permissions = interaction.permissions
            if not (permissions.view_channel and permissions.send_messages and permissions.use_application_commands):
                raise UserError('Потрібні View Channel, Send Messages і Use Application Commands.')
            current, _ = resolve_command(self.cog.bot.tree, self.command.qualified_name.split(), self.guild_id)
            if current is not self.command:
                raise UserError('Команду змінено. Введіть її знову.')
            permission_check(current, permissions)
            if not await self.cog.bot.tree.interaction_check(interaction):
                raise UserError('Виконання команди відхилено.')
            # Preserve the interaction's initial response for the original handler.
            # Slow resolution fails before Discord's three-second response deadline.
            async with asyncio.timeout(2):
                namespace = await namespace_for(current, self.values, guild)
            if self.cog.closing:
                raise UserError('Модуль завершує роботу.')
            async with asyncio.timeout(120):
                await current._invoke_with_namespace(interaction, namespace)
        except TimeoutError:
            await private(interaction, 'Час очікування минув. Перевірте результат перед повторною спробою.')
        except Exception as error:
            await self.cog.bot.tree.on_error(interaction, error)
        finally:
            self.cog.pending.discard(self)
            self.cog.tasks.discard(task)


class PrefixCommands(commands.Cog):
    def __init__(self, bot, *, clock=time.monotonic):
        self.bot, self.clock = bot, clock
        self.pending = set()
        self.tasks = set()
        self.cooldowns = OrderedDict()
        self.closing = False

    async def cog_unload(self):
        self.closing = True
        for view in tuple(self.pending):
            view.stop()
        self.pending.clear()
        tasks = tuple(task for task in self.tasks if task is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _direct_private(self, message, command, values):
        """Send select read-only commands by DM; do not manufacture interactions.

        If DM delivery is unavailable, keep the existing author-only button
        so slash handlers can still return ephemeral responses safely.
        """
        name = command.qualified_name
        if name not in DIRECT_PRIVATE:
            return False
        guide = self.bot.get_cog('Guide') if name in {'help', 'modules'} else None
        music = self.bot.get_cog('Music') if name == 'music queue' else None
        if ((name in {'help', 'modules'} and (command.parent is not None
                or guide is None or guide.closing))
                or (name == 'music queue' and (music is None or music._closing))):
            return False
        guild = message.guild
        if (guild.unavailable or self.bot.get_guild(guild.id) is not guild
                or getattr(message.channel, 'guild', None) is not guild
                or not isinstance(message.channel, (discord.TextChannel, discord.Thread))):
            raise UserError('Сервер або канал недоступний для цієї команди.')

        async def member_with_access():
            async with asyncio.timeout(5):
                member = await guild.fetch_member(message.author.id)
            if (not isinstance(member, discord.Member) or member.bot or member.pending
                    or member.guild is not guild or member.id != message.author.id
                    or guild.unavailable or self.bot.get_guild(guild.id) is not guild):
                raise UserError('Не вдалося підтвердити доступ учасника до сервера.')
            permissions = message.channel.permissions_for(member)
            if not (permissions.view_channel and permissions.send_messages
                    and permissions.use_application_commands):
                raise UserError('Потрібні View Channel, Send Messages і Use Application Commands.')
            permission_check(command, permissions)
            return member

        try:
            await member_with_access()
        except (discord.HTTPException, TimeoutError):
            return False

        if name == 'help':
            category, page = values.get('topic', 'general'), values.get('page', 1)
            if category not in CATEGORY_LABELS or type(page) is not int or not 1 <= page <= 10:
                raise UserError('Оберіть доступний розділ і сторінку довідки.')
            entries = collect_commands(self.bot.tree, guild.id)
            maximum = page_count(entries, category)
            if page > maximum:
                raise UserError(f'У цьому розділі сторінок: {maximum}.')
            payload = {'embed': render_help(entries, category, page)}
        elif name == 'modules':
            try:
                async with asyncio.timeout(5):
                    payload = {'embed': await collect_module_status(self.bot, guild)}
            except Exception as error:
                # Provider or database detail must never reach a public channel.
                log.warning('Prefix module read failed type=%s', type(error).__name__)
                return False
        else:
            try:
                # Reuse the slash command's actual voice membership checks and
                # private queue renderer; never create a fake Interaction.
                async with asyncio.timeout(12):
                    await music.voice_context(guild, message.author.id, allow_closing=True)
                    payload = {'content': music.queue_text(guild)}
            except Exception as error:
                log.warning('Prefix queue read unavailable type=%s', type(error).__name__)
                return False

        if self.closing or (guide is not None and guide.closing) or (music is not None and music._closing):
            return False
        try:
            # Fresh server membership and channel rights immediately before DM.
            member = await member_with_access()
            async with asyncio.timeout(10):
                await member.send(**payload, allowed_mentions=MENTIONS)
            return True
        except (discord.HTTPException, TimeoutError):
            return False

    async def handle_message(self, message):
        content = message.content
        if (not content.startswith('!') or not message.guild or message.author.bot or message.webhook_id):
            return False
        if self.closing or not isinstance(message.author, discord.Member) or message.author.pending:
            return True
        key, now = (message.guild.id, message.author.id), self.clock()
        previous = self.cooldowns.get(key)
        if previous is not None and now - previous < 3:
            return True
        self.cooldowns[key] = now
        self.cooldowns.move_to_end(key)
        if len(self.cooldowns) > 20000:
            self.cooldowns.popitem(last=False)
        if len(content) > 2000 or len(self.pending) >= MAX_PENDING or len(self.tasks) >= MAX_PENDING:
            return True
        if sum(view.guild_id == message.guild.id for view in self.pending) >= MAX_PER_GUILD:
            return True
        task = asyncio.current_task()
        self.tasks.add(task)
        view = None
        try:
            try:
                lexer = shlex.shlex(content[1:], posix=True)
                lexer.whitespace_split = True
                lexer.commenters = ''
                lexer.quotes = '"'
                words = list(lexer)
            except ValueError:
                raise UserError('Закрийте лапки в команді.') from None
            command, words = resolve_command(self.bot.tree, words, message.guild.id)
            values = parse_arguments(command, words)
            permission_check(command, message.author.guild_permissions)
            if await self._direct_private(message, command, values):
                return True
            view = PrefixView(self, message, command, values)
            self.pending.add(view)  # Reserve before the channel send can yield.
            async with asyncio.timeout(10):
                await message.channel.send('Команду підготовлено. Виконати може лише її автор; кнопка діє 2 хвилини.',
                    view=view, allowed_mentions=discord.AllowedMentions.none())
        except UserError as error:
            with suppress(discord.HTTPException, TimeoutError):
                async with asyncio.timeout(10):
                    await message.channel.send(str(error), allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, TimeoutError):
            if view is not None:
                view.stop()
                self.pending.discard(view)
        except BaseException:
            if view is not None:
                view.stop()
                self.pending.discard(view)
            raise
        finally:
            self.tasks.discard(task)
        return True
