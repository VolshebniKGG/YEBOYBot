"""Moderation use cases; Discord transport and SQL storage are injected."""
import asyncio
from dataclasses import dataclass, field
import logging
from urllib.parse import quote

KICK = 1 << 1
BAN = 1 << 2
ADMIN = 1 << 3
MODERATE = 1 << 40
log = logging.getLogger(__name__)


class ModerationError(ValueError):
    """Only controlled, safe user-facing messages are allowed here."""


@dataclass(frozen=True)
class MemberState:
    id: int
    guild_id: int
    rank: tuple
    permissions: int


@dataclass(frozen=True)
class Context:
    guild_id: int
    owner_id: int
    actor: MemberState
    bot: MemberState
    target: MemberState | None = None
    handle: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class KickResult:
    case_id: str
    code: str
    status: str | None = None
    message: str = ""


def valid_id(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise ModerationError("Вкажіть коректний числовий Discord ID.")
    return value


def audit_reason(case_id, actor_id, reason):
    # Keep the case link intact even for long Ukrainian/emoji reasons. The SDK
    # URL-encodes this header; the complete reason remains in the database.
    raw = f"case={case_id}; actor={actor_id}; {reason}"
    length, result = 0, []
    for character in raw:
        size = len(quote(character, safe="/ "))
        if length + size > 512:
            break
        result.append(character)
        length += size
    return "".join(result)


def _permission(member, owner_id, bits):
    return member.id == owner_id or bool(member.permissions & (bits | ADMIN))


def _context(context, guild_id, actor_id, target_id=None):
    if (context.guild_id != guild_id or context.actor.id != actor_id or
            context.actor.guild_id != guild_id or context.bot.guild_id != guild_id):
        raise ModerationError("Дані учасників не відповідають поточному серверу.")
    valid_id(context.owner_id)
    if target_id is not None and (context.target is None or context.target.id != target_id or
                                  context.target.guild_id != guild_id):
        raise ModerationError("Учасник не належить поточному серверу.")


def _kick_permissions(context):
    if not _permission(context.actor, context.owner_id, KICK):
        raise ModerationError("Вам потрібен дозвіл «Виганяти учасників».")
    if not _permission(context.bot, context.owner_id, KICK):
        raise ModerationError("Боту потрібен дозвіл «Виганяти учасників».")


def _target(context):
    member = context.target
    if member.id in {context.actor.id, context.bot.id, context.owner_id}:
        raise ModerationError("Не можна вигнати себе, цього бота або власника сервера.")
    if context.actor.id != context.owner_id and context.actor.rank <= member.rank:
        raise ModerationError("Роль учасника має бути нижчою за вашу.")
    if context.bot.rank <= member.rank:
        raise ModerationError("Роль бота має бути вищою за роль учасника.")


class KickService:
    def __init__(self, db, gateway):
        self.db = db
        self.gateway = gateway

    async def _load(self, guild_id, actor_id, target_id=None):
        try:
            async with asyncio.timeout(15):
                if target_id is None:
                    return await self.gateway.load(guild_id, actor_id)
                return await self.gateway.load(guild_id, actor_id, target_id)
        except ModerationError:
            raise
        except Exception:
            raise ModerationError("Не вдалося підтвердити актуальні дані Discord. Дію не надсилали.") from None

    async def _finish(self, guild_id, case_id, status):
        try:
            async with asyncio.timeout(10):
                return await self.db.finish_case(guild_id, case_id, status) == 1
        except Exception:
            log.warning("Moderation case finalization unconfirmed guild=%s case=%s", guild_id, case_id)
            return False

    async def kick(self, guild_id, actor_id, target_id, request_id, reason):
        for value in (guild_id, actor_id, target_id, request_id):
            valid_id(value)
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500 or "\0" in reason:
            raise ModerationError("Вкажіть причину від 1 до 500 символів.")
        reason = reason.strip()
        # Permissions precede even a duplicate receipt; former members need not be fetched.
        context = await self._load(guild_id, actor_id)
        _context(context, guild_id, actor_id)
        _kick_permissions(context)
        case_id = f"{guild_id:016x}{request_id:016x}"
        try:
            async with asyncio.timeout(10):
                row, created = await self.db.claim_case(guild_id, case_id, target_id, actor_id, "kick", reason)
        except Exception:
            return KickResult(case_id, "not_sent_unrecorded", message="Запис справи не підтверджено. Kick до Discord не надсилали; перевірте БД та історію.")
        if not created:
            return KickResult(case_id, "duplicate", row["status"], "Цей запит уже зареєстровано. Повторний kick не надсилали.")
        # No transaction spans REST. Recheck after the committed claim, immediately before DELETE.
        try:
            current = await self._load(guild_id, actor_id, target_id)
            _context(current, guild_id, actor_id, target_id)
            _kick_permissions(current)
            _target(current)
        except ModerationError as error:
            recorded = await self._finish(guild_id, case_id, "failed")
            return KickResult(case_id, "not_sent" if recorded else "not_sent_unrecorded",
                              "failed" if recorded else None, f"{error} Kick до Discord не надсилали.")
        try:
            # discord.py may retry transport failures internally; no application retry is used.
            async with asyncio.timeout(20):
                await self.gateway.kick(current, target_id, audit_reason(case_id, actor_id, reason))
        except asyncio.CancelledError:
            log.warning("Kick cancelled with unknown outcome guild=%s case=%s", guild_id, case_id)
            raise
        except Exception as error:
            log.warning("Kick outcome unconfirmed guild=%s case=%s error=%s", guild_id, case_id, type(error).__name__)
            return KickResult(case_id, "pending", "pending", "Результат Discord не підтверджено. Не повторюйте дію навмання; перевірте історію та Discord Audit Log.")
        if not await self._finish(guild_id, case_id, "applied"):
            return KickResult(case_id, "applied_unrecorded", message="Discord підтвердив kick, але запис підсумку справи не підтверджено. Не повторюйте дію; перевірте /modhistory.")
        return KickResult(case_id, "applied", "applied", "Discord підтвердив kick; результат збережено.")

    async def history(self, guild_id, actor_id, user_id=None, status=None, page=1):
        valid_id(guild_id)
        valid_id(actor_id)
        if user_id is not None:
            valid_id(user_id)
        if (status is not None and (not isinstance(status, str) or status not in {
                "pending", "applied", "failed", "revoked"})) or type(page) is not int or not 1 <= page <= 1000:
            raise ModerationError("Неправильний стан або номер сторінки історії.")
        context = await self._load(guild_id, actor_id)
        _context(context, guild_id, actor_id)
        if not _permission(context.actor, context.owner_id, KICK | BAN | MODERATE):
            raise ModerationError("Історія доступна власнику та учасникам із дозволами модерації.")
        try:
            async with asyncio.timeout(10):
                return await self.db.case_history(guild_id, user_id=user_id, status=status, page=page, page_size=5)
        except Exception:
            raise ModerationError("Не вдалося прочитати історію. Перевірте доступність БД.") from None
