"""Pinned discord.py adapter; fresh detached guilds avoid stale shared role caches."""
import asyncio
import math

import discord

from .moderation import Context, MemberState, ModerationError


class DiscordGateway:
    def __init__(self, client):
        self.client = client

    @staticmethod
    def _member(guild, member, expected_id):
        # Member.roles silently omits unknown IDs. Inspect the pinned SDK's raw
        # role-ID list only for this integrity check, without mutating its cache.
        raw_roles = getattr(member, "_roles", None)
        known = {role.id for role in guild.roles}
        if (member.id != expected_id or member.guild.id != guild.id or guild.id not in known or
                raw_roles is None or not set(raw_roles) <= known):
            raise ModerationError("Ролі або учасник змінилися під час перевірки. Оновіть дані й повторіть новий запит.")
        top = member.top_role
        rank = (0, 0, 0) if top.is_default() else (1, top.position, -top.id)
        # Public property also applies owner/admin and member-timeout semantics.
        return MemberState(member.id, guild.id, rank, member.guild_permissions.value)

    async def load(self, guild_id, actor_id, target_id=None):
        if self.client.user is None:
            raise ModerationError("Discord-клієнт ще не готовий до модерації.")
        try:
            guild = await self.client.fetch_guild(guild_id, with_counts=False)
            if guild.id != guild_id:
                raise ModerationError("Discord повернув інший сервер; дію заблоковано.")
            ids = list(dict.fromkeys([actor_id, self.client.user.id, *([] if target_id is None else [target_id])]))
            async with asyncio.TaskGroup() as group:
                fetched = {identifier: group.create_task(guild.fetch_member(identifier)) for identifier in ids}
            members = {identifier: self._member(guild, task.result(), identifier) for identifier, task in fetched.items()}
            return Context(guild.id, guild.owner_id, members[actor_id], members[self.client.user.id],
                           None if target_id is None else members[target_id], guild)
        except ModerationError:
            raise
        except Exception:
            raise ModerationError("Не вдалося оновити сервер, права або учасника через Discord. Перевірте доступ бота.") from None

    async def kick(self, context, target_id, audit_reason):
        await context.handle.kick(discord.Object(id=target_id), reason=audit_reason)


def history_embed(rows, page):
    embed = discord.Embed(title=f"Історія модерації · сторінка {page}",
                          description="Непідтверджені справи потребують звірки з Discord Audit Log; автоматичного повтору немає. Запис помилки сам собою не доводить скасування дії.")
    states = {"pending": "Непідтверджено", "applied": "Застосовано", "failed": "Зафіксовано помилку", "revoked": "Відкликано"}
    actions = {"kick": "Kick", "warn": "Попередження", "timeout": "Timeout", "untimeout": "Зняття timeout"}
    def stamp(value):
        return f"<t:{int(value)}:f>" if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else "Невідомо"
    def clip(value, budget):
        # Count UTF-16 units conservatively so non-BMP emoji also fit Discord fields.
        if len(value.encode("utf-16-le", errors="replace")) // 2 <= budget:
            return value
        result, length = [], 0
        for character in value:
            units = len(character.encode("utf-16-le", errors="replace")) // 2
            if length + units > budget - 1:
                break
            result.append(character)
            length += units
        return "".join(result) + "…"
    for row in rows[:5]:
        reason = clip(discord.utils.escape_markdown(discord.utils.escape_mentions(str(row["reason"]))), 600)
        case_id = discord.utils.escape_markdown(str(row["id"]))[:64]
        value = (f"Стан: {states.get(row['status'], 'Невідомо')}\n"
                 f"Учасник: `{row['user_id']}` · Модератор: `{row['actor_id']}`\n"
                 f"Створено: {stamp(row['created'])}\nЗмінено: {stamp(row.get('updated'))}\nПричина: {reason}")
        embed.add_field(name=f"{actions.get(row['action'], 'Інша дія')} · {case_id}", value=value, inline=False)
    if not rows:
        embed.description = "Справ за цими фільтрами на цій сторінці немає."
    return embed
