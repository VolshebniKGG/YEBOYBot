"""Bounded, fresh Discord reads and exact-ID deletion for confirmed clear jobs."""
import asyncio
from dataclasses import dataclass
from urllib.parse import quote

import discord

from .discord_moderation import DiscordGateway
from .moderation import ModerationError, valid_id
from .clear_plan import MAX_MESSAGES, MAX_AGE_SECONDS


@dataclass(frozen=True)
class Selection:
    message_ids: tuple[int, ...]
    skipped_pinned: int = 0
    skipped_old: int = 0
    skipped_system: int = 0


def _now():
    return discord.utils.utcnow()


def _payload_id(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal() and 1 <= len(value) <= 19:
        value = int(value)
    return valid_id(value)


def _eligible_type(message):
    return message.type in {discord.MessageType.default, discord.MessageType.reply}


class ClearGateway:
    def __init__(self, client):
        self.client = client
        self.moderation = DiscordGateway(client)

    async def _channel(self, guild_id, actor_id, channel_id):
        for identifier in (guild_id, actor_id, channel_id):
            valid_id(identifier)
        try:
            context = await self.moderation.load(guild_id, actor_id)
            data = await self.client.http.get_channel(channel_id)
            if (type(data.get("type")) is not int or data["type"] != 0 or
                    _payload_id(data.get("id")) != channel_id or
                    _payload_id(data.get("guild_id")) != guild_id):
                raise ModerationError("Очищення доступне лише у звичайному текстовому каналі цього сервера.")
            guild = context.handle
            channel = discord.TextChannel(state=self.client._connection, guild=guild, data=data)
            identifiers = list(dict.fromkeys((actor_id, context.bot.id)))
            async with asyncio.TaskGroup() as group:
                reads = {identifier: group.create_task(guild.fetch_member(identifier)) for identifier in identifiers}
            for identifier, task in reads.items():
                member = task.result()
                DiscordGateway._member(guild, member, identifier)
                permissions = channel.permissions_for(member)
                if not (permissions.manage_messages and permissions.view_channel and permissions.read_message_history):
                    raise ModerationError("Автору й боту потрібні Manage Messages, View Channel та Read Message History у цьому каналі.")
            return channel
        except ModerationError:
            raise
        except Exception:
            raise ModerationError("Не вдалося підтвердити актуальний канал або дозволи Discord.") from None

    async def check(self, guild_id, actor_id, channel_id):
        await self._channel(guild_id, actor_id, channel_id)

    def _message(self, channel, data, expected_id=None):
        # Public SDK helpers bind the supplied channel even when raw channel_id
        # differs. Validate the pinned REST payload before constructing a model.
        identifier = _payload_id(data.get("id"))
        if (_payload_id(data.get("channel_id")) != channel.id or
                (data.get("guild_id") is not None and _payload_id(data["guild_id"]) != channel.guild.id) or
                (expected_id is not None and identifier != expected_id)):
            raise ModerationError("Discord повернув повідомлення іншого каналу або запиту.")
        return self.client._connection.create_message(channel=channel, data=data)

    @staticmethod
    def _ids(plan):
        for identifier in (plan.guild_id, plan.actor_id, plan.channel_id, plan.request_id):
            valid_id(identifier)
        ids = plan.message_ids
        if not isinstance(ids, tuple) or not 1 <= len(ids) <= MAX_MESSAGES:
            raise ModerationError("Некоректний збережений список повідомлень.")
        for identifier in ids:
            valid_id(identifier)
            if identifier >= plan.request_id:
                raise ModerationError("Повідомлення має передувати початковому запиту очищення.")
        if len(set(ids)) != len(ids):
            raise ModerationError("Некоректний збережений список повідомлень.")
        return ids

    @staticmethod
    def _age(identifier):
        age = (_now() - discord.utils.snowflake_time(identifier)).total_seconds()
        if age < 0:
            raise ModerationError("Час повідомлення не відповідає поточному запиту.")
        return age

    async def snapshot(self, guild_id, actor_id, channel_id, request_id, limit):
        valid_id(request_id)
        if type(limit) is not int or not 1 <= limit <= MAX_MESSAGES:
            raise ModerationError("Вкажіть від 1 до 25 повідомлень.")
        channel = await self._channel(guild_id, actor_id, channel_id)
        try:
            # One bounded REST page, with the same before snowflake used by the
            # SDK history iterator. Never fetch extra pages to replace skipped IDs.
            data = await self.client.http.logs_from(channel.id, limit, before=request_id)
            if not isinstance(data, list) or len(data) > limit:
                raise ModerationError("Discord повернув неочікуваний обсяг історії.")
            selected, seen = [], set()
            pinned = old = system = 0
            for payload in data:
                message = self._message(channel, payload)
                if message.id >= request_id or message.id in seen:
                    raise ModerationError("Історія Discord не відповідає межам запиту.")
                seen.add(message.id)
                age = self._age(message.id)
                if message.pinned:
                    pinned += 1
                elif age > MAX_AGE_SECONDS:
                    old += 1
                elif not _eligible_type(message):
                    system += 1
                else:
                    selected.append(message.id)
            return Selection(tuple(selected), pinned, old, system)
        except ModerationError:
            raise
        except Exception:
            raise ModerationError("Не вдалося прочитати перевірену історію каналу.") from None

    async def validate(self, plan):
        identifiers = self._ids(plan)
        channel = await self._channel(plan.guild_id, plan.actor_id, plan.channel_id)
        try:
            async def fetch(identifier):
                data = await self.client.http.get_message(channel.id, identifier)
                message = self._message(channel, data, identifier)
                if message.pinned or self._age(identifier) > MAX_AGE_SECONDS or not _eligible_type(message):
                    raise ModerationError("Список змінився: закріплене, старе або системне повідомлення не видалятиметься.")
            async with asyncio.TaskGroup() as group:
                for identifier in identifiers:
                    group.create_task(fetch(identifier))
        except Exception:
            # Includes TaskGroup exception groups; never disclose raw REST data.
            raise ModerationError("Не вдалося повторно підтвердити весь список. Створіть новий запит очищення.") from None

    async def delete(self, plan, audit_reason):
        identifiers = self._ids(plan)
        if not isinstance(audit_reason, str) or not audit_reason or "\0" in audit_reason:
            raise ModerationError("Не підтверджено причину очищення.")
        try:
            if len(quote(audit_reason, safe="/ ")) > 512:
                raise ValueError("Audit header too long")
        except (ValueError, UnicodeError):
            raise ModerationError("Причина очищення перевищує ліміт журналу Discord.") from None
        channel = await self._channel(plan.guild_id, plan.actor_id, plan.channel_id)
        if any(self._age(identifier) > MAX_AGE_SECONDS for identifier in identifiers):
            raise ModerationError("Повідомлення вийшло за вікову межу очищення.")
        # Keep SDK/transport failures unwrapped: the service records an unknown
        # outcome and must not replay the operation. No new history is selected.
        if len(identifiers) == 1:
            # Pinned SDK delete_messages drops reason on its single-ID branch.
            await self.client.http.delete_message(channel.id, identifiers[0], reason=audit_reason)
        else:
            await channel.delete_messages([discord.Object(id=value) for value in identifiers], reason=audit_reason)
