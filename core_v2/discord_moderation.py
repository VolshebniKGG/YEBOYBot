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
        return await self._load(guild_id, actor_id, target_id)

    async def load_ban(self, guild_id, actor_id, target_id):
        return await self._load(guild_id, actor_id, target_id, allow_absent_target=True)

    async def _load(self, guild_id, actor_id, target_id, *, allow_absent_target=False):
        if self.client.user is None:
            raise ModerationError("Discord-клієнт ще не готовий до модерації.")
        try:
            guild = await self.client.fetch_guild(guild_id, with_counts=False)
            if guild.id != guild_id:
                raise ModerationError("Discord повернув інший сервер; дію заблоковано.")
            bot_id = self.client.user.id
            ids = list(dict.fromkeys([actor_id, bot_id, *([] if target_id is None else [target_id])]))
            absent = object()

            async def fetch(identifier):
                try:
                    return await guild.fetch_member(identifier)
                except discord.NotFound as error:
                    # A non-member can be banned by ID. Unknown guild/access or
                    # an absent actor/bot must never be interpreted as that case.
                    if (allow_absent_target and identifier == target_id and
                            identifier not in {actor_id, bot_id} and
                            error.status == 404 and error.code == 10007):
                        return absent
                    raise

            async with asyncio.TaskGroup() as group:
                fetched = {identifier: group.create_task(fetch(identifier)) for identifier in ids}
            members = {identifier: self._member(guild, task.result(), identifier)
                       for identifier, task in fetched.items() if task.result() is not absent}
            return Context(guild.id, guild.owner_id, members[actor_id], members[bot_id],
                           members.get(target_id), guild)
        except ModerationError:
            raise
        except Exception:
            raise ModerationError("Не вдалося оновити сервер, права або учасника через Discord. Перевірте доступ бота.") from None

    async def kick(self, context, target_id, audit_reason):
        await context.handle.kick(discord.Object(id=target_id), reason=audit_reason)

    async def ban(self, context, target_id, audit_reason):
        # discord.py otherwise defaults to deleting one day of message history.
        await context.handle.ban(discord.Object(id=target_id), reason=audit_reason,
                                 delete_message_seconds=0)

    async def unban(self, context, target_id, audit_reason):
        await context.handle.unban(discord.Object(id=target_id), reason=audit_reason)

    async def ban_exists(self, context, target_id):
        try:
            entry = await context.handle.fetch_ban(discord.Object(id=target_id))
            if entry.user.id != target_id:
                raise ModerationError("Discord повернув блокування іншого користувача; дію заблоковано.")
            return True
        except discord.NotFound as error:
            if error.status == 404 and error.code == 10026:
                return False
        except ModerationError:
            raise
        except Exception:
            pass
        raise ModerationError("Не вдалося підтвердити блокування через Discord. Дію не надсилали.") from None


def history_embed(rows, page):
    embed = discord.Embed(title=f"Історія модерації · сторінка {page}",
                          description="Непідтверджені справи потребують звірки з Discord Audit Log; автоматичного повтору немає. Запис помилки сам собою не доводить скасування дії.")
    states = {"pending": "Непідтверджено", "applied": "Застосовано", "failed": "Зафіксовано помилку", "revoked": "Відкликано"}
    actions = {"kick": "Kick", "ban": "Блокування", "unban": "Зняття блокування",
               "warn": "Попередження", "timeout": "Timeout", "untimeout": "Зняття timeout"}
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
