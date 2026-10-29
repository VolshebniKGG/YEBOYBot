"""Guild-scoped SQL operations; SQLite local, PostgreSQL for sustained load."""
import asyncio
import re
import sqlite3
import time
import uuid
from pathlib import Path
from sqlalchemy import (MetaData, Table, Column, BigInteger, Integer, String, Boolean,
                        Float, select, update, event, func, text, inspect, case)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

CURRENT_SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (1, 2)


class MigrationRequired(RuntimeError):
    """A supported older database requires an explicit backed-up migration."""


class WarningConflict(ValueError):
    """A warning request key is already bound to different immutable input."""


meta = MetaData()
versions = Table("v2_schema", meta, Column("version", Integer, primary_key=True))
settings = Table("v2_settings", meta,
    Column("guild_id", BigInteger, primary_key=True),
    Column("levels", Boolean, nullable=False, default=True),
    Column("automod", Boolean, nullable=False, default=False),
    Column("log_channel", BigInteger), Column("welcome_channel", BigInteger), Column("autorole", BigInteger))
xp = Table("v2_xp", meta,
    Column("guild_id", BigInteger, primary_key=True), Column("user_id", BigInteger, primary_key=True),
    Column("text_xp", BigInteger, nullable=False, default=0),
    Column("voice_xp", BigInteger, nullable=False, default=0),
    Column("last_text", Float, nullable=False, default=0),
    Column("last_voice", Float, nullable=False, default=0))
cases = Table("v2_cases", meta,
    Column("id", String(32), primary_key=True),
    Column("guild_id", BigInteger, nullable=False, index=True),
    Column("user_id", BigInteger, nullable=False, index=True),
    Column("actor_id", BigInteger, nullable=False), Column("action", String(32), nullable=False),
    Column("reason", String(500), nullable=False), Column("created", Float, nullable=False),
    Column("updated", Float, nullable=False, server_default=text("0")),
    Column("status", String(20), nullable=False))
jobs = Table("v2_jobs", meta,
    Column("id", String(32), primary_key=True),
    Column("guild_id", BigInteger, nullable=False, index=True),
    Column("user_id", BigInteger, nullable=False), Column("channel_id", BigInteger, nullable=False),
    Column("body", String(1000), nullable=False), Column("due", Float, nullable=False, index=True),
    Column("lease", Float, nullable=False, default=0),
    Column("attempts", Integer, nullable=False, default=0),
    Column("state", String(20), nullable=False, default="pending"))
panels = Table("v2_role_panels", meta,
    Column("id", String(32), primary_key=True),
    Column("guild_id", BigInteger, nullable=False, index=True),
    Column("role_id", BigInteger, nullable=False))


def required_columns(version):
    """Column requirements for each supported on-disk version."""
    if type(version) is not int or version not in SUPPORTED_SCHEMA_VERSIONS:
        raise RuntimeError("Unsupported schema version")
    result = {table.name: {column.name for column in table.columns} for table in meta.sorted_tables}
    if version == 1:
        result["v2_cases"].remove("updated")
    return result


def _schema_state(table_names, view_names, columns, current):
    """Return 0/fresh or the validated supported on-disk version, without writes."""
    existing = table_names | view_names
    if not any(name.lower().startswith("v2_") for name in existing):
        return 0
    if "v2_schema" not in table_names or "version" not in columns.get("v2_schema", set()):
        raise RuntimeError("Unsupported schema metadata; restore compatible code or a verified backup")
    if len(current) != 1 or type(current[0]) is not int or current[0] not in SUPPORTED_SCHEMA_VERSIONS:
        raise RuntimeError("Unsupported schema version; restore compatible code or a verified backup")
    version = current[0]
    for name, required in required_columns(version).items():
        if name not in table_names or not required <= columns.get(name, set()):
            raise RuntimeError("Unsupported schema structure; restore compatible code or a verified backup")
    if version == 1 and "updated" in columns.get("v2_cases", set()):
        raise RuntimeError("Unsupported schema structure; ambiguous partially migrated v1")
    return version


def sqlite_version(cursor):
    # Read existing metadata before journal_mode=WAL can alter a rejected file.
    cursor.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view')")
    objects = cursor.fetchall()
    table_names = {name for name, kind in objects if kind == "table"}
    view_names = {name for name, kind in objects if kind == "view"}
    columns = {}
    for table in meta.sorted_tables:
        if table.name in table_names:
            cursor.execute(f'PRAGMA table_info("{table.name}")')
            columns[table.name] = {row[1] for row in cursor.fetchall()}
    current = []
    if "version" in columns.get("v2_schema", set()):
        cursor.execute("SELECT version FROM v2_schema LIMIT 2")
        current = [row[0] for row in cursor.fetchall()]
    return _schema_state(table_names, view_names, columns, current)


def _connection_schema_state(connection):
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    view_names = set(inspector.get_view_names())
    if connection.dialect.name == "postgresql":
        view_names.update(inspector.get_materialized_view_names())
        view_names.update(inspector.get_foreign_table_names())
    columns = {table.name: {column["name"] for column in inspector.get_columns(table.name)}
               for table in meta.sorted_tables if table.name in table_names}
    current = []
    if "version" in columns.get("v2_schema", set()):
        current = connection.execute(select(versions.c.version).limit(2)).scalars().all()
    return _schema_state(table_names, view_names, columns, current)


def _require_current_schema(version):
    if version and version != CURRENT_SCHEMA_VERSION:
        raise MigrationRequired("Schema migration required before startup; use the backed-up migration menu")


class Database:
    def __init__(self, url):
        self.sqlite = url.startswith("sqlite")
        if self.sqlite and ":memory:" not in url:
            Path(url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        kwargs = {"connect_args": {"timeout": 30}} if self.sqlite else {
            "pool_size": 10, "max_overflow": 10, "pool_timeout": 15}
        self.engine = create_async_engine(url, pool_pre_ping=True, **kwargs)
        if self.sqlite:
            @event.listens_for(self.engine.sync_engine, "connect")
            def pragmas(connection, _):
                for attempt in range(4):
                    cursor = connection.cursor()
                    try:
                        cursor.execute("PRAGMA busy_timeout=30000")
                        version = sqlite_version(cursor)
                        _require_current_schema(version)
                        cursor.execute("PRAGMA journal_mode=WAL")
                        return
                    except sqlite3.OperationalError as error:
                        code = getattr(error, "sqlite_errorcode", None)
                        busy = isinstance(code, int) and (code & 0xff) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                        if not busy or attempt == 3:
                            raise
                    finally:
                        cursor.close()
                    # Journal transitions may ignore busy_timeout. Close the cursor
                    # and yield through the async driver before retrying the guard.
                    connection.run_async(lambda _: asyncio.sleep(0.05 * 2**attempt))

    def insert(self, table):
        return sqlite_insert(table) if self.sqlite else pg_insert(table)

    async def initialize(self):
        async with self.engine.begin() as conn:
            if self.sqlite:
                # Real transaction also covers DDL under sqlite legacy transaction control.
                await conn.execute(text("BEGIN IMMEDIATE"))
            else:
                await conn.execute(text("SELECT pg_advisory_xact_lock(91027602)"))
            # Recheck pooled connections: another tool may have changed the schema.
            # create_all is only an initial bootstrap, never an implicit migration.
            existing = await conn.run_sync(_connection_schema_state)
            _require_current_schema(existing)
            if not existing:
                await conn.run_sync(meta.create_all)
                await conn.execute(self.insert(versions).values(version=CURRENT_SCHEMA_VERSION).on_conflict_do_nothing())

    async def close(self):
        await self.engine.dispose()

    async def health(self):
        async with self.engine.connect() as conn:
            return await conn.scalar(select(1)) == 1

    async def get_settings(self, guild_id):
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(settings).where(settings.c.guild_id == guild_id))).mappings().first()
        return dict(row) if row else dict(guild_id=guild_id, levels=True, automod=False,
                                          log_channel=None, welcome_channel=None, autorole=None)

    async def configure(self, guild_id, **values):
        if not values or not set(values) <= {"levels", "automod", "log_channel", "welcome_channel", "autorole"}:
            raise ValueError("Invalid settings")
        stmt = self.insert(settings).values(guild_id=guild_id, **values)
        stmt = stmt.on_conflict_do_update(index_elements=[settings.c.guild_id], set_=values)
        async with self.engine.begin() as conn:
            await conn.execute(stmt)

    async def award(self, guild_id, user_id, kind="text", now=None):
        if kind not in {"text", "voice"}:
            raise ValueError("Invalid XP kind")
        now = time.time() if now is None else now
        counter, last = f"{kind}_xp", f"last_{kind}"
        stmt = self.insert(xp).values(guild_id=guild_id, user_id=user_id, **{counter: 5, last: now})
        stmt = stmt.on_conflict_do_update(index_elements=[xp.c.guild_id, xp.c.user_id],
            set_={counter: xp.c[counter] + 5, last: now}, where=xp.c[last] <= now - 60
            ).returning(xp.c[counter])
        async with self.engine.begin() as conn:
            return (await conn.execute(stmt)).scalar_one_or_none()

    async def rank(self, guild_id, user_id):
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(xp).where(xp.c.guild_id == guild_id,
                xp.c.user_id == user_id))).mappings().first()
        return dict(row) if row else {"text_xp": 0, "voice_xp": 0}

    async def rank_snapshot(self, guild_id, user_id):
        """One statement snapshots both XP counters and deterministic guild ranks.

        Equal counters are ordered by user_id. Reading an unranked member never
        creates a row: it is ranked as zero XP among this guild's stored members.
        """
        if any(type(value) is not int or not 0 < value < 2**63
               for value in (guild_id, user_id)):
            raise ValueError("Invalid rank scope")
        profile = select(*(
            func.coalesce(select(xp.c[counter]).where(
                xp.c.guild_id == guild_id, xp.c.user_id == user_id).scalar_subquery(), 0).label(counter)
            for counter in ("text_xp", "voice_xp"))).cte("rank_profile")
        other = xp.alias("rank_other")
        positions = []
        for kind in ("text", "voice"):
            counter = f"{kind}_xp"
            ahead = select(func.count()).select_from(other).where(
                other.c.guild_id == guild_id,
                (other.c[counter] > profile.c[counter]) |
                ((other.c[counter] == profile.c[counter]) & (other.c.user_id < user_id)))
            positions.append((ahead.correlate(profile).scalar_subquery() + 1).label(f"{kind}_rank"))
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(
                profile.c.text_xp, profile.c.voice_xp, *positions))).mappings().one()
            return dict(row)

    async def leaderboard(self, guild_id, page=1, *, kind="total"):
        """Read one guild page, ordered exactly like the selected rank axis.

        Each XP counter fits a non-negative signed 64-bit integer, while their
        total can require 64 unsigned bits. Sort that total as a carry and an
        int64 remainder: SQLite must not promote an overflowing sum to a float,
        and PostgreSQL must not evaluate an overflowing bigint addition.
        """
        if type(guild_id) is not int or not 0 < guild_id < 2**63:
            raise ValueError("Invalid leaderboard scope")
        if type(page) is not int or not 1 <= page <= 1000:
            raise ValueError("Invalid page")
        if type(kind) is not str or kind not in {"total", "text", "voice"}:
            raise ValueError("Invalid XP kind")
        if kind == "total":
            remaining = (2**63 - 1) - xp.c.voice_xp
            overflow = xp.c.text_xp > remaining
            carry = case((overflow, 1), else_=0)
            remainder = case((overflow, xp.c.text_xp - remaining - 1),
                             else_=xp.c.text_xp + xp.c.voice_xp)
            ordering = (carry.desc(), remainder.desc(), xp.c.user_id.asc())
        else:
            ordering = (xp.c[f"{kind}_xp"].desc(), xp.c.user_id.asc())
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(xp).where(xp.c.guild_id == guild_id)
                .order_by(*ordering).limit(10).offset((page-1)*10))
            return [dict(r) for r in rows.mappings()]

    async def create_case(self, guild_id, user_id, actor_id, action, reason, status="pending"):
        case_id = uuid.uuid4().hex
        now = time.time()
        async with self.engine.begin() as conn:
            await conn.execute(cases.insert().values(id=case_id, guild_id=guild_id, user_id=user_id,
                actor_id=actor_id, action=action, reason=reason[:500], created=now, updated=now, status=status))
        return case_id

    async def claim_case(self, guild_id, case_id, user_id, actor_id, action, reason):
        """Atomically reserve an audited request; retries never overwrite its row."""
        if any(type(value) is not int or not 0 < value < 2**63
               for value in (guild_id, user_id, actor_id)):
            raise ValueError("Invalid moderation scope")
        if not isinstance(case_id, str) or not re.fullmatch(r"[0-9a-f]{32}", case_id):
            raise ValueError("Invalid moderation case ID")
        if (not isinstance(action, str) or action not in {"kick", "ban", "unban"}
                or not isinstance(reason, str) or not reason.strip()
                or len(reason.strip()) > 500 or "\0" in reason):
            raise ValueError("Invalid moderation request")
        reason = reason.strip()
        now = time.time()
        statement = self.insert(cases).values(id=case_id, guild_id=guild_id,
            user_id=user_id, actor_id=actor_id, action=action, reason=reason,
            created=now, updated=now, status="pending")
        statement = statement.on_conflict_do_nothing(index_elements=[cases.c.id]).returning(cases.c.id)
        async with self.engine.begin() as connection:
            inserted = (await connection.execute(statement)).scalar_one_or_none()
            row = (await connection.execute(select(cases).where(
                cases.c.id == case_id, cases.c.guild_id == guild_id))).mappings().first()
            if row is None or any(row[name] != value for name, value in {
                    "user_id": user_id, "actor_id": actor_id, "action": action, "reason": reason}.items()):
                raise ValueError("Moderation case ID is already bound to a different request")
            return dict(row), inserted is not None

    async def record_warning(self, guild_id, case_id, user_id, actor_id, reason):
        """Commit one applied local warning; re-delivery never changes its row."""
        if any(type(value) is not int or not 0 < value < 2**63
               for value in (guild_id, user_id, actor_id)):
            raise ValueError("Invalid warning scope")
        if not isinstance(case_id, str) or not re.fullmatch(r"[0-9a-f]{32}", case_id):
            raise ValueError("Invalid warning case ID")
        if (not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500 or
                any(ord(character) < 32 or 127 <= ord(character) <= 159 or
                    0xD800 <= ord(character) <= 0xDFFF for character in reason)):
            raise ValueError("Invalid warning reason")
        reason = reason.strip()
        now = time.time()
        statement = self.insert(cases).values(id=case_id, guild_id=guild_id,
            user_id=user_id, actor_id=actor_id, action="warn", reason=reason,
            created=now, updated=now, status="applied")
        statement = statement.on_conflict_do_nothing(index_elements=[cases.c.id]).returning(cases.c.id)
        async with self.engine.begin() as connection:
            inserted = (await connection.execute(statement)).scalar_one_or_none()
            row = (await connection.execute(select(cases).where(
                cases.c.id == case_id, cases.c.guild_id == guild_id))).mappings().first()
            if row is None or any(row[name] != value for name, value in {
                    "user_id": user_id, "actor_id": actor_id,
                    "action": "warn", "reason": reason}.items()):
                raise WarningConflict("Warning request is already bound to different input")
            return dict(row), inserted is not None

    async def case_history(self, guild_id, user_id=None, status=None, page=1, page_size=5):
        """Bounded read of this guild's cases, including unresolved pending rows."""
        if type(guild_id) is not int or not 0 < guild_id < 2**63:
            raise ValueError("Invalid moderation scope")
        if user_id is not None and (type(user_id) is not int or not 0 < user_id < 2**63):
            raise ValueError("Invalid moderation target")
        if status is not None and (not isinstance(status, str) or status not in {
                "pending", "applied", "failed", "revoked"}):
            raise ValueError("Invalid moderation status")
        if type(page) is not int or not 1 <= page <= 1000 or type(page_size) is not int or not 1 <= page_size <= 10:
            raise ValueError("Invalid moderation page")
        statement = select(cases).where(cases.c.guild_id == guild_id)
        if user_id is not None:
            statement = statement.where(cases.c.user_id == user_id)
        if status is not None:
            statement = statement.where(cases.c.status == status)
        statement = statement.order_by(cases.c.created.desc(), cases.c.id.desc())
        statement = statement.limit(page_size).offset((page - 1) * page_size)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
            return [dict(row) for row in rows]

    async def finish_case(self, guild_id, case_id, status):
        if status not in {"applied", "failed"}:
            raise ValueError("Invalid status")
        async with self.engine.begin() as conn:
            return (await conn.execute(update(cases).where(cases.c.guild_id == guild_id,
                cases.c.id == case_id, cases.c.status == "pending").values(status=status, updated=time.time()))).rowcount

    async def warnings(self, guild_id, user_id):
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(cases).where(cases.c.guild_id == guild_id,
                cases.c.user_id == user_id, cases.c.action == "warn", cases.c.status == "applied"
                ).order_by(cases.c.created.desc(), cases.c.id.desc()).limit(10))
            return [dict(r) for r in rows.mappings()]

    async def revoke_warning(self, guild_id, case_id):
        async with self.engine.begin() as conn:
            return (await conn.execute(update(cases).where(cases.c.guild_id == guild_id,
                cases.c.id == case_id, cases.c.action == "warn", cases.c.status == "applied"
                ).values(status="revoked", updated=time.time()))).rowcount

    async def schedule(self, guild_id, user_id, channel_id, body, due):
        if not 1 <= len(body) <= 1000:
            raise ValueError("Invalid reminder body")
        job_id = uuid.uuid4().hex
        async with self.engine.begin() as conn:
            if self.sqlite:
                await conn.execute(text("BEGIN IMMEDIATE"))
            else:
                await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                                   {"key": (guild_id ^ user_id) & ((1 << 63)-1)})
            count = await conn.scalar(select(func.count()).select_from(jobs).where(
                jobs.c.guild_id == guild_id, jobs.c.user_id == user_id, jobs.c.state == "pending"))
            if count >= 10:
                raise ValueError("Максимум 10 активних нагадувань на сервері.")
            await conn.execute(jobs.insert().values(id=job_id, guild_id=guild_id,
                user_id=user_id, channel_id=channel_id, body=body, due=due))
        return job_id

    async def claim_jobs(self, guild_ids, now=None):
        if not guild_ids:
            return []
        now = time.time() if now is None else now
        async with self.engine.begin() as conn:
            if self.sqlite:
                await conn.execute(text("BEGIN IMMEDIATE"))
            query = select(jobs.c.id).where(jobs.c.guild_id.in_(guild_ids), jobs.c.state == "pending",
                jobs.c.due <= now, jobs.c.lease <= now).order_by(jobs.c.due).limit(20)
            if not self.sqlite:
                query = query.with_for_update(skip_locked=True)
            ids = (await conn.execute(query)).scalars().all()
            if not ids:
                return []
            rows = await conn.execute(update(jobs).where(jobs.c.id.in_(ids)).values(
                lease=now+120, attempts=jobs.c.attempts+1).returning(jobs))
            return [dict(r) for r in rows.mappings()]

    async def finish_job(self, guild_id, job_id, attempt, success, permanent=False):
        async with self.engine.begin() as conn:
            await conn.execute(update(jobs).where(jobs.c.guild_id == guild_id, jobs.c.id == job_id,
                jobs.c.attempts == attempt, jobs.c.state == "pending").values(lease=0,
                state="done" if success else ("failed" if permanent or attempt >= 5 else "pending"),
                due=time.time()+min(3600, 30*2**attempt)))

    async def reminders(self, guild_id, user_id):
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(jobs).where(jobs.c.guild_id == guild_id,
                jobs.c.user_id == user_id, jobs.c.state == "pending").order_by(jobs.c.due).limit(10))
            return [dict(r) for r in rows.mappings()]

    async def cancel_reminder(self, guild_id, user_id, job_id):
        async with self.engine.begin() as conn:
            return (await conn.execute(update(jobs).where(jobs.c.guild_id == guild_id,
                jobs.c.user_id == user_id, jobs.c.id == job_id, jobs.c.state == "pending",
                jobs.c.lease <= time.time()).values(state="cancelled"))).rowcount

    async def create_panel(self, guild_id, role_id):
        panel = uuid.uuid4().hex
        async with self.engine.begin() as conn:
            await conn.execute(panels.insert().values(id=panel, guild_id=guild_id, role_id=role_id))
        return panel

    async def panel(self, guild_id, panel):
        async with self.engine.connect() as conn:
            return await conn.scalar(select(panels.c.role_id).where(panels.c.guild_id == guild_id,
                                                                   panels.c.id == panel))
