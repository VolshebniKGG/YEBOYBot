"""Durable, guild/actor/channel-bound manual clear jobs; no schema changes."""
import math
import time

from sqlalchemy import func, select, text, update

from .clear_plan import (CLEAR_STATES, CONFIRM_SECONDS, MAX_ACTIVE_CONFIRMATIONS,
                         ClearLimitError, ClearPlan, valid_key)
from .moderation import valid_id


def _scope(guild_id, actor_id, channel_id, request_id):
    for value in (guild_id, actor_id, channel_id):
        valid_id(value)
    valid_key(request_id)


def _now(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid clear clock")
    return value


def _record(row):
    if row is None:
        return None
    try:
        return {"plan": ClearPlan.decode(dict(row)), "state": row["state"]}
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ValueError("Unsupported stored clear request") from None


class ClearRepository:
    def __init__(self, database):
        # Keep the existing Database independent of this optional workflow.
        from .db import jobs
        self.database = database
        self.jobs = jobs

    def _where(self, guild_id, actor_id, channel_id, request_id):
        _scope(guild_id, actor_id, channel_id, request_id)
        table = self.jobs
        return (table.c.id == request_id, table.c.guild_id == guild_id,
                table.c.user_id == actor_id, table.c.channel_id == channel_id,
                table.c.state.in_(CLEAR_STATES))

    async def _lock(self, connection, guild_id, actor_id, *, prepare=False):
        if self.database.sqlite:
            await connection.execute(text("BEGIN IMMEDIATE"))
        elif prepare:
            await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                                     {"key": (guild_id ^ actor_id) & ((1 << 63) - 1)})

    async def prepare(self, plan):
        if type(plan) is not ClearPlan:
            raise ValueError("Invalid clear plan")
        body = plan.encode()
        # Validate even a caller-mutated dataclass before opening a transaction.
        candidate = _record({"id": plan.id, "guild_id": plan.guild_id,
                             "user_id": plan.actor_id, "channel_id": plan.channel_id,
                             "body": body, "due": plan.expires, "state": "clear_confirm"})
        plan = candidate["plan"]
        table = self.jobs
        async with self.database.engine.begin() as connection:
            await self._lock(connection, plan.guild_id, plan.actor_id, prepare=True)
            now = _now(time.time())
            await connection.execute(update(table).where(
                table.c.guild_id == plan.guild_id, table.c.user_id == plan.actor_id,
                table.c.state == "clear_confirm", table.c.due <= now).values(state="clear_expired"))
            existing = (await connection.execute(select(table).where(table.c.id == plan.id))).mappings().first()
            if existing is not None:
                saved = _record(existing)
                original = saved["plan"]
                if any(getattr(original, name) != getattr(plan, name) for name in (
                        "id", "guild_id", "actor_id", "channel_id", "request_id", "reason", "requested")):
                    raise ValueError("Clear request is already bound to different input")
                return saved
            if not now < plan.expires <= now + CONFIRM_SECONDS + .001:
                raise ValueError("Invalid clear confirmation lifetime")
            count = await connection.scalar(select(func.count()).select_from(table).where(
                table.c.guild_id == plan.guild_id, table.c.user_id == plan.actor_id,
                table.c.state == "clear_confirm", table.c.due > now))
            if count >= MAX_ACTIVE_CONFIRMATIONS:
                raise ClearLimitError("Clear confirmation capacity reached")
            # ON CONFLICT also protects a malicious/cross-scope ID collision.
            inserted = (await connection.execute(self.database.insert(table).values(
                id=plan.id, guild_id=plan.guild_id, user_id=plan.actor_id,
                channel_id=plan.channel_id, body=body, due=plan.expires,
                lease=0, attempts=0, state="clear_confirm").on_conflict_do_nothing(
                    index_elements=[table.c.id]).returning(table.c.id))).scalar_one_or_none()
            if inserted is None:
                raise ValueError("Clear request is already bound to different input")
            return candidate

    async def get(self, guild_id, actor_id, channel_id, request_id):
        where = self._where(guild_id, actor_id, channel_id, request_id)
        async with self.database.engine.connect() as connection:
            row = (await connection.execute(select(self.jobs).where(*where))).mappings().first()
            return _record(row)

    async def claim(self, guild_id, actor_id, channel_id, request_id, now):
        where = self._where(guild_id, actor_id, channel_id, request_id)
        now = _now(now)
        table = self.jobs
        async with self.database.engine.begin() as connection:
            await self._lock(connection, guild_id, actor_id)
            query = select(table).where(*where)
            if not self.database.sqlite:
                query = query.with_for_update()
            row = (await connection.execute(query)).mappings().first()
            record = _record(row)
            if record is None or record["state"] != "clear_confirm":
                return record, False
            # Lock acquisition may outlive the button deadline. Never use an
            # earlier caller timestamp to accept a confirmation after expiry.
            now = max(now, _now(time.time()))
            state = "clear_expired" if record["plan"].expires <= now else "clear_running"
            values = {"state": state}
            if state == "clear_running":
                values.update(lease=0, attempts=table.c.attempts + 1)
            updated = (await connection.execute(update(table).where(
                *where, table.c.state == "clear_confirm").values(**values).returning(table))).mappings().first()
            return _record(updated), state == "clear_running"

    async def cancel(self, guild_id, actor_id, channel_id, request_id, now):
        where = self._where(guild_id, actor_id, channel_id, request_id)
        now = _now(now)
        table = self.jobs
        async with self.database.engine.begin() as connection:
            await self._lock(connection, guild_id, actor_id)
            query = select(table).where(*where)
            if not self.database.sqlite:
                query = query.with_for_update()
            row = (await connection.execute(query)).mappings().first()
            record = _record(row)
            if record is None or record["state"] != "clear_confirm":
                return record
            now = max(now, _now(time.time()))
            state = "clear_expired" if record["plan"].expires <= now else "clear_cancelled"
            updated = (await connection.execute(update(table).where(
                *where, table.c.state == "clear_confirm").values(state=state).returning(table))).mappings().first()
            return _record(updated)

    async def finish(self, guild_id, actor_id, channel_id, request_id, state):
        where = self._where(guild_id, actor_id, channel_id, request_id)
        if not isinstance(state, str) or state not in {"clear_done", "clear_pending", "clear_failed"}:
            raise ValueError("Invalid clear terminal state")
        async with self.database.engine.begin() as connection:
            return (await connection.execute(update(self.jobs).where(
                *where, self.jobs.c.state == "clear_running").values(state=state, lease=0))).rowcount
