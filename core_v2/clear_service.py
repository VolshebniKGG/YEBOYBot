"""Durable, explicitly confirmed message removal; never replay unknown writes."""
import asyncio
from dataclasses import dataclass
import logging
import time
from urllib.parse import quote

from .clear_plan import (ClearPlan, ClearLimitError, CONFIRM_SECONDS, MAX_MESSAGES,
                         request_key, valid_key, valid_reason)
from .moderation import ModerationError, valid_id

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClearResult:
    id: str
    code: str
    state: str | None = None
    count: int = 0
    message: str = ""


def _scope(guild_id, actor_id, channel_id, identifier):
    for value in (guild_id, actor_id, channel_id):
        valid_id(value)
    try:
        valid_key(identifier)
        if int(identifier[:16], 16) != guild_id:
            raise ValueError("Wrong scope")
    except (TypeError, ValueError):
        raise ModerationError("Вкажіть ID власного запиту очищення на цьому сервері й у цьому каналі.") from None


def clear_audit_reason(plan):
    raw = f"clear={plan.id}; actor={plan.actor_id}; channel={plan.channel_id}; {plan.reason}"
    result, length = [], 0
    for character in raw:
        size = len(quote(character, safe="/ "))
        if length + size > 512:
            break
        result.append(character)
        length += size
    return "".join(result)


class ClearService:
    def __init__(self, repository, gateway, clock=time.time):
        self.repository, self.gateway, self.clock = repository, gateway, clock

    async def _database(self, method, *arguments):
        try:
            async with asyncio.timeout(10):
                return await getattr(self.repository, method)(*arguments)
        except ClearLimitError:
            raise ModerationError("Максимум 5 активних підтверджень очищення на сервері. Скасуйте попереднє або дочекайтеся завершення 60 секунд.") from None
        except Exception as error:
            log.warning("Clear repository unavailable operation=%s error=%s", method, type(error).__name__)
            raise ModerationError("Не вдалося перевірити або зберегти стан запиту. Перевірте БД та /clear-status; не повторюйте дію навмання.") from None

    async def _check(self, guild_id, actor_id, channel_id):
        try:
            async with asyncio.timeout(15):
                await self.gateway.check(guild_id, actor_id, channel_id)
        except ModerationError:
            raise
        except Exception:
            raise ModerationError("Не вдалося підтвердити права або текстовий канал Discord. Видалення не запускали.") from None

    def _effective(self, record):
        if record["state"] == "clear_confirm" and self.clock() >= record["plan"].expires:
            return dict(record, state="clear_expired")
        return record

    async def prepare(self, guild_id, actor_id, channel_id, interaction_id, amount, reason):
        for value in (guild_id, actor_id, channel_id, interaction_id):
            valid_id(value)
        try:
            reason = valid_reason(reason)
            if type(amount) is not int or not 1 <= amount <= MAX_MESSAGES:
                raise ValueError("Invalid count")
        except ValueError:
            raise ModerationError("Вкажіть кількість від 1 до 25 і причину від 1 до 200 символів без керівних символів.") from None
        identifier = request_key(guild_id, interaction_id)
        # Authorize before reading even an old duplicate receipt.
        await self._check(guild_id, actor_id, channel_id)
        previous = await self._database("get", guild_id, actor_id, channel_id, identifier)
        if previous is not None:
            plan = previous["plan"]
            if plan.reason != reason or plan.requested != amount:
                raise ModerationError("Цей запит уже має інші підтверджувані параметри; його не змінено.")
            return self._effective(previous)
        try:
            async with asyncio.timeout(30):
                selected = await self.gateway.snapshot(guild_id, actor_id, channel_id, interaction_id, amount)
            if not selected.message_ids:
                raise ModerationError("Серед переглянутих повідомлень немає придатних: закріплені, системні та старші 7 діб зберігаються.")
            plan = ClearPlan(identifier, guild_id, actor_id, channel_id, interaction_id, reason,
                             amount, tuple(selected.message_ids), self.clock() + CONFIRM_SECONDS,
                             selected.skipped_pinned, selected.skipped_old, selected.skipped_system)
        except ModerationError:
            raise
        except Exception:
            raise ModerationError("Не вдалося підготувати обмежений список повідомлень. Видалення не запускали.") from None
        record = await self._database("prepare", plan)
        return self._effective(record)

    async def _finish(self, guild_id, actor_id, channel_id, identifier, state):
        try:
            async with asyncio.timeout(10):
                return await self.repository.finish(guild_id, actor_id, channel_id, identifier, state) == 1
        except Exception as error:
            log.warning("Clear finalization unconfirmed request=%s error=%s", identifier, type(error).__name__)
            return False

    async def execute(self, guild_id, actor_id, channel_id, identifier):
        _scope(guild_id, actor_id, channel_id, identifier)
        await self._check(guild_id, actor_id, channel_id)
        record, won = await self._database("claim", guild_id, actor_id, channel_id, identifier, self.clock())
        if record is None:
            raise ModerationError("Власного запиту з цим ID у цьому каналі не знайдено.")
        if not won:
            return ClearResult(identifier, "duplicate", record["state"],
                               len(record["plan"].message_ids) if record["state"] == "clear_done" else 0,
                               "Запит уже прийнятий, скасований або прострочений. Повторне видалення не запускали; перевірте /clear-status.")
        plan = record["plan"]
        try:
            # Fetch only the approved IDs; never select a replacement page.
            async with asyncio.timeout(90):
                await self.gateway.validate(plan)
        except asyncio.CancelledError:
            # Durable running state is intentionally not resumed after shutdown.
            raise
        except Exception:
            recorded = await self._finish(guild_id, actor_id, channel_id, identifier, "clear_failed")
            return ClearResult(identifier, "not_sent" if recorded else "not_sent_unrecorded",
                               "clear_failed" if recorded else None,
                               message="Права або схвалений список повідомлень не підтверджено. Запит видалення не надсилали; створіть новий перегляд.")
        try:
            async with asyncio.timeout(60):
                await self.gateway.delete(plan, clear_audit_reason(plan))
        except asyncio.CancelledError:
            log.warning("Clear cancelled with unknown outcome request=%s", identifier)
            raise
        except Exception as error:
            log.warning("Clear outcome unconfirmed request=%s error=%s", identifier, type(error).__name__)
            recorded = await self._finish(guild_id, actor_id, channel_id, identifier, "clear_pending")
            return ClearResult(identifier, "pending", "clear_pending" if recorded else None,
                               message="Результат Discord не підтверджено. Не повторюйте видалення навмання; перевірте /clear-status і журнал Discord.")
        count = len(plan.message_ids)
        if not await self._finish(guild_id, actor_id, channel_id, identifier, "clear_done"):
            return ClearResult(identifier, "api_confirmed_db_unknown", count=count,
                               message=f"Discord підтвердив запит для {count} повідомлень, але запис підсумку в БД не підтверджено. Не повторюйте дію.")
        return ClearResult(identifier, "confirmed", "clear_done", count,
                           f"Discord підтвердив запит для {count} повідомлень. Це кількість схвалених ID, а не незалежний підрахунок фактичних видалень.")

    async def cancel(self, guild_id, actor_id, channel_id, identifier):
        _scope(guild_id, actor_id, channel_id, identifier)
        record = await self._database("cancel", guild_id, actor_id, channel_id, identifier, self.clock())
        if record is None:
            raise ModerationError("Власного запиту з цим ID у цьому каналі не знайдено.")
        return ClearResult(identifier, "cancelled" if record["state"] == "clear_cancelled" else "duplicate",
                           record["state"], len(record["plan"].message_ids) if record["state"] == "clear_done" else 0,
                           "Запит перевірено. Скасування не запускає видалення й не відкочує вже надісланий запит.")

    async def status(self, guild_id, actor_id, channel_id, identifier):
        _scope(guild_id, actor_id, channel_id, identifier)
        record = await self._database("get", guild_id, actor_id, channel_id, identifier)
        if record is None:
            raise ModerationError("Власного запиту з цим ID у цьому каналі не знайдено.")
        return self._effective(record)
