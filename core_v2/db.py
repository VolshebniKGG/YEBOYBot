"""Guild-scoped SQL operations; SQLite local, PostgreSQL for sustained load."""
import asyncio
import sqlite3
import time
import uuid
from pathlib import Path
from sqlalchemy import (MetaData, Table, Column, BigInteger, Integer, String, Boolean,
                        Float, select, update, event, func, text, inspect)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

CURRENT_SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (1, 2)


class MigrationRequired(RuntimeError):
    """A supported older database requires an explicit backed-up migration."""


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

    async def leaderboard(self, guild_id, page=1):
        if not 1 <= page <= 1000:
            raise ValueError("Invalid page")
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(xp).where(xp.c.guild_id == guild_id)
                .order_by((xp.c.text_xp + xp.c.voice_xp).desc(), xp.c.user_id).limit(10).offset((page-1)*10))
            return [dict(r) for r in rows.mappings()]

    async def create_case(self, guild_id, user_id, actor_id, action, reason, status="pending"):
        case_id = uuid.uuid4().hex
        now = time.time()
        async with self.engine.begin() as conn:
            await conn.execute(cases.insert().values(id=case_id, guild_id=guild_id, user_id=user_id,
                actor_id=actor_id, action=action, reason=reason[:500], created=now, updated=now, status=status))
        return case_id

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
                ).order_by(cases.c.created.desc()).limit(10))
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
