import asyncio
import os
import time

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from core_v2.db import Database, meta, jobs, cases, xp


@pytest.fixture
async def db(tmp_path):
    url = os.getenv("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    if url.startswith("postgresql") and make_url(url).database != "yeboy_test":
        raise RuntimeError("Tests require a disposable database named yeboy_test")
    database = Database(url)
    if not database.sqlite:
        async with database.engine.begin() as conn:
            await conn.run_sync(meta.drop_all)
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


async def test_settings_are_guild_scoped(db):
    await db.configure(1, levels=False)
    await db.configure(2, log_channel=99)
    await db.configure(2, automod=True)
    assert not (await db.get_settings(1))["levels"]
    assert (await db.get_settings(1))["log_channel"] is None
    assert (await db.get_settings(2))["log_channel"] == 99
    assert (await db.get_settings(2))["levels"]


async def test_concurrent_xp_awards_have_one_winner(db):
    results = await asyncio.gather(*(db.award(1, 2, now=1000) for _ in range(20)))
    assert results.count(5) == 1
    assert (await db.rank(1, 2))["text_xp"] == 5
    assert await db.award(1, 2, now=1059) is None
    assert await db.award(1, 2, now=1060) == 10
    assert (await db.rank(2, 2))["text_xp"] == 0


async def test_text_voice_counters_and_read_are_independent(db):
    await db.award(1, 2, now=1000)
    await db.award(1, 2, "voice", now=1000)
    before = await db.rank(1, 2)
    assert before["text_xp"] == before["voice_xp"] == 5
    assert before == await db.rank(1, 2)
    with pytest.raises(ValueError):
        await db.award(1, 2, "invalid")


@pytest.mark.parametrize("kind", ["text", "voice"])
@pytest.mark.parametrize("headroom", [0, 1, 4, 5, 6])
async def test_xp_award_saturates_imported_bigint_without_losing_precision(db, kind, headroom):
    maximum = 2**63 - 1
    counter = f"{kind}_xp"
    async with db.engine.begin() as conn:
        await conn.execute(xp.insert().values(guild_id=1, user_id=2,
                                             **{counter: maximum - headroom}))
    expected = min(maximum, maximum - headroom + 5)
    assert await db.award(1, 2, kind, now=1000) == expected
    stored = (await db.rank(1, 2))[counter]
    assert type(stored) is int
    assert stored == expected
    assert await db.award(1, 2, kind, now=1059) is None


async def test_leaderboard_scope(db):
    await db.award(1, 5)
    await db.award(2, 6)
    assert [r["user_id"] for r in await db.leaderboard(1)] == [5]


async def test_warnings_scope_and_state(db):
    case = await db.create_case(1, 2, 3, "warn", "reason", "applied")
    assert not await db.warnings(2, 2)
    assert not await db.revoke_warning(2, case)
    assert len(await db.warnings(1, 2)) == 1
    assert await db.revoke_warning(1, case) == 1
    assert not await db.warnings(1, 2)
    assert not await db.revoke_warning(1, case)


async def test_case_pending_is_not_applied_warning(db):
    case = await db.create_case(1, 2, 3, "warn", "reason")
    assert not await db.warnings(1, 2)
    assert not await db.finish_case(2, case, "applied")
    assert await db.finish_case(1, case, "applied")
    assert not await db.finish_case(1, case, "failed")
    async with db.engine.connect() as conn:
        assert await conn.scalar(select(cases.c.status).where(cases.c.id == case)) == "applied"


async def test_claim_scope_and_concurrency(db):
    first = await db.schedule(1, 2, 3, "hello", 1)
    await db.schedule(2, 2, 3, "other", 1)
    batches = await asyncio.gather(db.claim_jobs([1], 10), db.claim_jobs([1], 10))
    assert [r["id"] for batch in batches for r in batch] == [first]
    assert not await db.claim_jobs([], 10)
    assert not await db.claim_jobs([1], 129)
    retry = await db.claim_jobs([1], 130)
    assert retry[0]["attempts"] == 2


async def test_old_worker_cannot_finish_new_attempt(db):
    job = await db.schedule(1, 2, 3, "hello", 1)
    await db.claim_jobs([1], 10)
    await db.claim_jobs([1], 130)
    await db.finish_job(1, job, 1, True)
    assert len(await db.reminders(1, 2)) == 1
    await db.finish_job(2, job, 2, True)
    assert len(await db.reminders(1, 2)) == 1
    await db.finish_job(1, job, 2, True)
    assert not await db.reminders(1, 2)


async def test_crashed_reminder_workers_exhaust_retry_budget(db):
    job = await db.schedule(1, 2, 3, "hello", 1)
    other = await db.schedule(2, 2, 3, "other guild", 1)
    async with db.engine.begin() as conn:
        await conn.execute(jobs.update().where(jobs.c.id == other).values(attempts=5))
    for attempt in range(1, 6):
        batch = await db.claim_jobs([1], 10 + (attempt - 1) * 120)
        assert len(batch) == 1
        assert batch[0]["attempts"] == attempt
    # The final worker still owns its lease and can finish normally.
    assert not await db.claim_jobs([1], 609)
    assert len(await db.reminders(1, 2)) == 1
    assert not await db.claim_jobs([1], 610)
    assert not await db.reminders(1, 2)
    async with db.engine.connect() as conn:
        states = dict((await conn.execute(select(jobs.c.id, jobs.c.state))).all())
    assert states == {job: "failed", other: "pending"}
    # A late completion cannot resurrect a terminal job.
    await db.finish_job(1, job, 5, True)
    async with db.engine.connect() as conn:
        assert await conn.scalar(select(jobs.c.state).where(jobs.c.id == job)) == "failed"


async def test_reminder_cancel_scope_and_active_lease(db):
    job = await db.schedule(1, 2, 3, "hello", 1)
    assert not await db.cancel_reminder(2, 2, job)
    assert not await db.cancel_reminder(1, 4, job)
    await db.claim_jobs([1])
    assert not await db.cancel_reminder(1, 2, job)
    async with db.engine.begin() as conn:
        await conn.execute(jobs.update().where(jobs.c.id == job).values(lease=0))
    assert await db.cancel_reminder(1, 2, job)


async def test_reminder_cap_is_atomic(db):
    results = await asyncio.gather(*(db.schedule(1, 2, 3, "x", time.time()+60)
                                    for _ in range(15)), return_exceptions=True)
    assert sum(isinstance(r, str) for r in results) == 10
    assert sum(isinstance(r, ValueError) for r in results) == 5
    assert await db.schedule(2, 2, 3, "other guild", 1)


async def test_jobs_survive_restart(db):
    await db.schedule(1, 2, 3, "persist", 1)
    await db.close()
    await db.initialize()
    assert len(await db.claim_jobs([1])) == 1


async def test_schema_rejects_newer_version(db):
    async with db.engine.begin() as conn:
        await conn.execute(text("UPDATE v2_schema SET version = 999"))
    with pytest.raises(RuntimeError, match="Unsupported schema"):
        await db.initialize()
