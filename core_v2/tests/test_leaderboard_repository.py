"""Exact rank axes and total ordering on disposable SQLite/PostgreSQL data."""
import os
import random
from unittest.mock import MagicMock
import uuid

import pytest
from sqlalchemy import event, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from core_v2.db import Database, xp


MAX_XP = 2**63 - 1


@pytest.fixture(params=["sqlite", "postgres"])
async def repository(request, tmp_path):
    if request.param == "sqlite":
        database = Database("sqlite+aiosqlite:///" + str(tmp_path / "Рейтинг з пробілами.db"))
        try:
            await database.initialize()
            yield database
        finally:
            await database.close()
        return
    configured = os.getenv("TEST_DATABASE_URL", "")
    if not configured.startswith("postgresql"):
        pytest.skip("Disposable PostgreSQL not configured")
    url = make_url(configured)
    if url.drivername != "postgresql+asyncpg" or url.database != "yeboy_test":
        raise RuntimeError("Leaderboard SQL tests require disposable yeboy_test")
    namespace = "leaderboard_test_" + uuid.uuid4().hex
    admin = create_async_engine(url)
    database = Database(url.render_as_string(hide_password=False))
    await database.close()
    database.engine = create_async_engine(url, connect_args={"server_settings": {"search_path": namespace}})
    created = False
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
            created = True
        await database.initialize()
        yield database
    finally:
        await database.close()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{namespace}" CASCADE'))
        await admin.dispose()


async def populate(database, values, guild_id=11):
    async with database.engine.begin() as connection:
        await connection.execute(insert(xp), [dict(guild_id=guild_id, user_id=user,
            text_xp=chat, voice_xp=voice, last_text=123, last_voice=456)
            for user, chat, voice in values])


async def stored(database):
    async with database.engine.connect() as connection:
        return [dict(row) for row in (await connection.execute(
            select(xp).order_by(xp.c.guild_id, xp.c.user_id))).mappings()]


async def all_pages(database, *, kind="total", guild_id=11):
    rows = []
    for page in range(1, 1001):
        current = await database.leaderboard(guild_id, page, kind=kind)
        rows.extend(current)
        if len(current) < 10:
            break
    return rows


async def test_total_preserves_exact_order_at_and_above_signed_64_bit_limit(repository):
    await populate(repository, [(10, MAX_XP, MAX_XP - 2), (20, MAX_XP, MAX_XP - 1),
        (30, MAX_XP, MAX_XP), (40, MAX_XP, 1), (50, MAX_XP, 0),
        (60, MAX_XP - 1, 1), (70, MAX_XP - 2, 1)])
    rows = await repository.leaderboard(11)
    assert [row["user_id"] for row in rows] == [30, 20, 10, 40, 50, 60, 70]
    assert [row["text_xp"] + row["voice_xp"] for row in rows] == [
        2 * MAX_XP, 2 * MAX_XP - 1, 2 * MAX_XP - 2, MAX_XP + 1,
        MAX_XP, MAX_XP, MAX_XP - 1]


async def test_large_adjacent_totals_do_not_become_a_float_tie(repository):
    # Both integer additions overflow on PostgreSQL and round to the same
    # SQLite float with the previous expression. The greater total must win.
    await populate(repository, [(10, MAX_XP, MAX_XP - 1), (20, MAX_XP, MAX_XP)])
    assert [row["user_id"] for row in await repository.leaderboard(11)] == [20, 10]


async def test_full_total_order_matches_arbitrary_precision_python_sum(repository):
    source = random.Random(17823)
    values = [(user, source.randrange(MAX_XP + 1), source.randrange(MAX_XP + 1))
              for user in range(1, 121)]
    values.extend([(121, 0, 0), (122, MAX_XP, 0), (123, 0, MAX_XP),
                   (124, MAX_XP, MAX_XP), (125, MAX_XP, 1), (126, 1, MAX_XP)])
    await populate(repository, values)
    expected = [user for user, _, _ in sorted(values, key=lambda row: (-(row[1] + row[2]), row[0]))]
    assert [row["user_id"] for row in await all_pages(repository)] == expected


async def test_default_total_call_remains_compatible(repository):
    await populate(repository, [(1, 100, 0), (2, 0, 200), (3, 120, 120)])
    old_call = await repository.leaderboard(11, 1)
    explicit = await repository.leaderboard(11, 1, kind="total")
    assert old_call == explicit
    assert [row["user_id"] for row in old_call] == [3, 2, 1]


@pytest.mark.parametrize("kind", ["text", "voice"])
async def test_axis_places_match_rank_card_snapshot(repository, kind):
    await populate(repository, [(user, (user % 4) * 100, ((20 - user) % 5) * 50)
                                for user in range(1, 14)])
    await populate(repository, [(1, MAX_XP, MAX_XP), (22, MAX_XP, MAX_XP)], guild_id=22)
    rows = await all_pages(repository, kind=kind)
    assert len(rows) == 13
    for position, row in enumerate(rows, 1):
        snapshot = await repository.rank_snapshot(11, row["user_id"])
        assert snapshot[f"{kind}_rank"] == position
        assert snapshot[f"{kind}_xp"] == row[f"{kind}_xp"]


@pytest.mark.parametrize("kind", ["total", "text", "voice"])
async def test_equal_scores_use_user_id_instead_of_insert_order(repository, kind):
    await populate(repository, [(99, 100, 50), (3, 100, 50), (50, 100, 50), (1, 100, 50)])
    assert [row["user_id"] for row in await repository.leaderboard(11, kind=kind)] == [1, 3, 50, 99]


@pytest.mark.parametrize("kind", ["total", "text", "voice"])
async def test_pagination_is_bounded_and_guild_scoped(repository, kind):
    await populate(repository, [(user, user * 5, user * 10) for user in range(1, 24)])
    await populate(repository, [(1, MAX_XP, MAX_XP), (99, MAX_XP, MAX_XP)], guild_id=22)
    pages = [await repository.leaderboard(11, page, kind=kind) for page in (1, 2, 3, 4, 1000)]
    assert [len(rows) for rows in pages] == [10, 10, 3, 0, 0]
    assert [row["user_id"] for rows in pages[:3] for row in rows] == list(range(23, 0, -1))
    assert all(row["guild_id"] == 11 for rows in pages for row in rows)


@pytest.mark.parametrize("kind", ["total", "text", "voice"])
async def test_read_is_one_select_without_modifying_xp_or_timestamps(repository, kind):
    await populate(repository, [(1, MAX_XP, MAX_XP), (2, 20, 30)])
    before = await stored(repository)
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(repository.engine.sync_engine, "before_cursor_execute", capture)
    try:
        rows = await repository.leaderboard(11, kind=kind)
    finally:
        event.remove(repository.engine.sync_engine, "before_cursor_execute", capture)
    assert len(rows) == 2
    assert len(statements) == 1 and statements[0].lstrip().upper().startswith("SELECT")
    assert await stored(repository) == before


@pytest.mark.parametrize("kind", ["total", "text", "voice"])
async def test_empty_guild_does_not_create_profile_or_settings(repository, kind):
    assert await repository.leaderboard(11, kind=kind) == []
    assert await stored(repository) == []


@pytest.mark.parametrize("field,value", [
    ("guild_id", True), ("guild_id", 0), ("guild_id", -1),
    ("guild_id", 2**63), ("guild_id", "11"), ("guild_id", 11.0),
    ("page", True), ("page", False), ("page", 0), ("page", -1),
    ("page", 1001), ("page", "1"), ("page", 1.0),
    ("kind", None), ("kind", True), ("kind", []), ("kind", "TEXT"),
    ("kind", "text DESC"), ("kind", "total; DROP TABLE v2_xp"),
], ids=["scope-bool", "scope-zero", "scope-negative", "scope-oversize", "scope-string", "scope-float",
        "page-true", "page-false", "page-zero", "page-negative", "page-oversize", "page-string", "page-float",
        "kind-none", "kind-bool", "kind-list", "kind-case", "kind-order", "kind-sql"])
async def test_invalid_inputs_are_rejected_before_any_sql(repository, monkeypatch, field, value):
    connect = MagicMock(side_effect=AssertionError("No SQL for invalid input"))
    monkeypatch.setattr(type(repository.engine), "connect", connect)
    values = dict(guild_id=11, page=1, kind="total")
    values[field] = value
    with pytest.raises(ValueError):
        await repository.leaderboard(**values)
    connect.assert_not_called()
