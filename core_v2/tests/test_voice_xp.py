"""Measured conversation eligibility and SQL-backed rank/voice counters."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import discord
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from core_v2.db import Database, xp
from core_v2 import voice_xp as module
from core_v2.voice_xp import VoiceXP, eligible_members


@pytest.fixture(params=["sqlite", "postgres"])
async def repository(request, tmp_path):
    if request.param == "sqlite":
        database = Database("sqlite+aiosqlite:///" + str(tmp_path / "Голосовий XP.db"))
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
        raise RuntimeError("Voice XP tests require disposable yeboy_test")
    namespace = "voice_xp_test_" + uuid.uuid4().hex
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


class Clock:
    value = 100.0

    def __call__(self):
        return self.value


def channel(guild, channel_id, members=(), *, kind=discord.ChannelType.voice):
    result = SimpleNamespace(id=channel_id, type=kind, members=[])
    guild.voice_channels.append(result)
    for user_id in members:
        member = SimpleNamespace(id=user_id, bot=False, guild=guild, voice=SimpleNamespace(
            channel=result, mute=False, deaf=False, self_mute=False, self_deaf=False, suppress=False))
        result.members.append(member)
    return result


def harness(*, members=(2, 3), db=None):
    guild = SimpleNamespace(id=1, shard_id=0, voice_channels=[], unavailable=False, afk_channel=None)
    room = channel(guild, 10, members)
    bot = SimpleNamespace(guilds=[guild], is_ready=lambda: True,
                          guild_settings=AsyncMock(return_value={"levels": True}),
                          db=db or SimpleNamespace(award=AsyncMock(return_value=5)))
    clock = Clock()
    return VoiceXP(bot, clock=clock), clock, guild, room


async def minutes(cog, clock, count=1):
    for _ in range(count * 6):
        clock.value += 10
        await cog.poll()


async def test_full_observed_minute_then_five_xp_each_without_catchup():
    cog, clock, guild, room = harness()
    await cog.poll()
    cog.bot.db.award.assert_not_awaited()
    for _ in range(5):
        clock.value += 10
        await cog.poll()
    cog.bot.db.award.assert_not_awaited()
    clock.value += 10
    await cog.poll()
    assert cog.bot.db.award.await_count == 2
    assert {call.args for call in cog.bot.db.award.await_args_list} == {(1, 2), (1, 3)}
    assert all(call.kwargs == {"kind": "voice"} for call in cog.bot.db.award.await_args_list)
    await minutes(cog, clock)
    assert cog.bot.db.award.await_count == 4


@pytest.mark.parametrize("field", ["mute", "deaf", "self_mute", "self_deaf", "suppress", "bot"])
async def test_ineligible_second_human_prevents_solo_farming(field):
    cog, clock, guild, room = harness()
    if field == "bot":
        room.members[1].bot = True
    else:
        setattr(room.members[1].voice, field, True)
    await cog.poll()
    await minutes(cog, clock, 2)
    assert not cog.sessions
    cog.bot.db.award.assert_not_awaited()


def test_eligibility_requires_same_normal_non_afk_channel_and_two_humans():
    cog, clock, guild, room = harness()
    assert eligible_members(guild) == {2: 10, 3: 10}
    guild.afk_channel = room
    assert eligible_members(guild) == {}
    guild.afk_channel = None
    room.type = discord.ChannelType.stage_voice
    assert eligible_members(guild) == {}
    room.type = discord.ChannelType.voice
    room.members[1].voice.channel = SimpleNamespace(id=99)
    assert eligible_members(guild) == {}
    room.members[1].voice = None
    assert eligible_members(guild) == {}
    guild.unavailable = True
    assert eligible_members(guild) == {}


async def test_solo_and_bot_channels_do_not_gain_xp():
    cog, clock, guild, room = harness(members=(2,))
    other = channel(guild, 11, (3, 4))
    other.members[1].bot = True
    await cog.poll()
    await minutes(cog, clock, 2)
    cog.bot.db.award.assert_not_awaited()


async def test_mute_unmute_resets_both_participants_continuous_minute():
    cog, clock, guild, room = harness()
    await cog.poll()
    await minutes(cog, clock)
    cog.bot.db.award.reset_mock()
    clock.value += 5
    room.members[0].voice.self_mute = True
    await cog.on_voice_state_update(room.members[0], None, None)
    assert not cog.sessions
    clock.value += 5
    room.members[0].voice.self_mute = False
    await cog.on_voice_state_update(room.members[0], None, None)
    for _ in range(5):
        clock.value += 10
        await cog.poll()
    cog.bot.db.award.assert_not_awaited()
    clock.value += 10
    await cog.poll()
    assert cog.bot.db.award.await_count == 2


async def test_joining_companion_starts_timer_then_leaving_resets_it():
    cog, clock, guild, room = harness(members=(2,))
    await cog.poll()
    await minutes(cog, clock)
    companion = channel(guild, 11, (3,)).members[0]
    guild.voice_channels.pop()
    companion.voice.channel = room
    room.members.append(companion)
    await cog.on_voice_state_update(companion, None, None)
    clock.value += 10
    await cog.poll()
    room.members.remove(companion)
    companion.voice.channel = None
    await cog.on_voice_state_update(companion, None, None)
    assert not cog.sessions
    await minutes(cog, clock)
    cog.bot.db.award.assert_not_awaited()


async def test_move_between_eligible_channels_resets_timer():
    cog, clock, guild, room = harness()
    destination = channel(guild, 11, (4, 5))
    await cog.poll()
    clock.value += 20
    await cog.poll()
    member = room.members.pop()
    member.voice.channel = destination
    destination.members.append(member)
    await cog.on_voice_state_update(member, None, None)
    assert (1, 2) not in cog.sessions
    assert cog.sessions[(1, 3)].since == clock.value
    assert cog.sessions[(1, 4)].since == 100


@pytest.mark.parametrize("event_name", ["on_disconnect", "on_guild_unavailable", "on_guild_remove", "on_shard_disconnect"])
async def test_disconnect_and_unavailable_events_erase_measurements(event_name):
    cog, clock, guild, room = harness()
    await cog.poll()
    if event_name == "on_disconnect":
        await cog.on_disconnect()
    elif event_name == "on_shard_disconnect":
        await cog.on_shard_disconnect(0)
    else:
        await getattr(cog, event_name)(guild)
    assert not cog.sessions
    await cog.poll()
    cog.bot.db.award.assert_not_awaited()


async def test_other_shard_disconnect_preserves_current_guild():
    cog, clock, guild, room = harness()
    await cog.poll()
    await cog.on_shard_disconnect(1)
    assert len(cog.sessions) == 2


@pytest.mark.parametrize("resume", ["on_shard_ready", "on_shard_resumed"])
async def test_disconnected_shard_never_uses_stale_cache_even_if_global_ready(resume):
    cog, clock, guild, room = harness()
    await cog.poll()
    await cog.on_shard_disconnect(0)
    await minutes(cog, clock, 2)
    assert not cog.sessions
    cog.bot.db.award.assert_not_awaited()
    await getattr(cog, resume)(0)
    await cog.poll()
    await minutes(cog, clock)
    assert cog.bot.db.award.await_count == 2


async def test_global_disconnect_ignores_stale_ready_until_resumed():
    cog, clock, guild, room = harness()
    await cog.poll()
    await cog.on_disconnect()
    await minutes(cog, clock, 2)
    await cog.on_voice_state_update(room.members[0], None, None)
    assert not cog.sessions
    cog.bot.db.award.assert_not_awaited()
    await cog.on_resumed()
    await cog.poll()
    await minutes(cog, clock)
    assert cog.bot.db.award.await_count == 2


@pytest.mark.parametrize("event_name", ["on_disconnect", "on_shard_disconnect", "remove"])
async def test_inflight_settings_cannot_recreate_timers_after_disconnect(event_name):
    cog, clock, guild, room = harness()
    entered, release = asyncio.Event(), asyncio.Event()

    async def settings(guild_id):
        entered.set()
        await release.wait()
        return {"levels": True}

    cog.bot.guild_settings.side_effect = settings
    polling = asyncio.create_task(cog.poll())
    await entered.wait()
    if event_name == "on_disconnect":
        await cog.on_disconnect()
    elif event_name == "on_shard_disconnect":
        await cog.on_shard_disconnect(0)
    else:
        cog.bot.guilds.clear()
    release.set()
    await polling
    assert not cog.sessions
    cog.bot.db.award.assert_not_awaited()


async def test_gateway_not_ready_and_missing_guild_drop_timers():
    cog, clock, guild, room = harness()
    await cog.poll()
    cog.bot.is_ready = lambda: False
    await cog.poll()
    assert not cog.sessions
    cog.bot.is_ready = lambda: True
    await cog.poll()
    cog.bot.guilds.clear()
    await cog.poll()
    assert not cog.sessions


@pytest.mark.parametrize("jump", [31, 3600, -1])
async def test_poll_gap_and_clock_rollback_do_not_award_unobserved_time(jump):
    cog, clock, guild, room = harness()
    await cog.poll()
    clock.value += jump
    await cog.poll()
    cog.bot.db.award.assert_not_awaited()
    assert all(session.since == clock.value for session in cog.sessions.values())


async def test_disabled_levels_and_settings_failures_reset_measurement(caplog):
    cog, clock, guild, room = harness()
    await cog.poll()
    cog.bot.guild_settings.return_value = {"levels": False}
    await cog.poll()
    assert not cog.sessions
    await cog.on_voice_state_update(room.members[0], None, None)
    assert not cog.sessions
    cog.bot.guild_settings.side_effect = RuntimeError("synthetic private database address")
    await cog.poll()
    assert not cog.sessions
    assert "synthetic private" not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_database_failure_waits_new_minute_without_logging_private_error(caplog):
    cog, clock, guild, room = harness()
    cog.bot.db.award.side_effect = RuntimeError("synthetic private database address")
    await cog.poll()
    await minutes(cog, clock)
    assert cog.bot.db.award.await_count == 2
    assert "synthetic private" not in caplog.text
    clock.value += 10
    await cog.poll()
    assert cog.bot.db.award.await_count == 2


async def test_database_timeout_is_contained_without_immediate_retry():
    cog, clock, guild, room = harness()
    cog.bot.db.award.side_effect = TimeoutError()
    await cog.poll()
    await minutes(cog, clock)
    assert cog.bot.db.award.await_count == 2
    clock.value += 10
    await cog.poll()
    assert cog.bot.db.award.await_count == 2


async def test_cancellation_propagates_without_being_reported_success():
    cog, clock, guild, room = harness()
    cog.bot.db.award.side_effect = asyncio.CancelledError()
    await cog.poll()
    for _ in range(5):
        clock.value += 10
        await cog.poll()
    clock.value += 10
    with pytest.raises(asyncio.CancelledError):
        await cog.poll()


async def test_session_and_award_limits_are_bounded(monkeypatch):
    cog, clock, guild, room = harness(members=(2, 3, 4, 5))
    monkeypatch.setattr(module, "MAX_SESSIONS", 3)
    monkeypatch.setattr(module, "MAX_AWARDS", 2)
    await cog.poll()
    assert len(cog.sessions) == 3
    await minutes(cog, clock)
    assert cog.bot.db.award.await_count == 2
    clock.value += 10
    await cog.poll()
    assert cog.bot.db.award.await_count == 3


async def test_listener_removes_departure_while_an_award_is_queued():
    cog, clock, guild, room = harness(members=tuple(range(2, 10)))
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_award(*args, **kwargs):
        entered.set()
        await release.wait()

    cog.bot.db.award.side_effect = slow_award
    await cog.poll()
    for _ in range(5):
        clock.value += 10
        await cog.poll()
    clock.value += 10
    polling = asyncio.create_task(cog.poll())
    await entered.wait()
    departed = room.members.pop()
    departed.voice.channel = None
    await cog.on_voice_state_update(departed, None, None)
    release.set()
    await polling
    assert departed.id not in {call.args[1] for call in cog.bot.db.award.await_args_list}


async def test_cog_unload_stops_collector_and_clears_state():
    cog, clock, guild, room = harness()
    cog.bot.wait_until_ready = AsyncMock()
    await cog.cog_load()
    assert cog.collect.is_running()
    await cog.cog_unload()
    assert not cog.collect.is_running()
    assert not cog.sessions


async def test_rank_snapshot_is_guild_scoped_and_orders_ties_by_id(repository):
    async with repository.engine.begin() as connection:
        await connection.execute(xp.insert(), [
            {"guild_id": 1, "user_id": 2, "text_xp": 10, "voice_xp": 50},
            {"guild_id": 1, "user_id": 3, "text_xp": 10, "voice_xp": 5},
            {"guild_id": 1, "user_id": 4, "text_xp": 25, "voice_xp": 5},
            {"guild_id": 2, "user_id": 3, "text_xp": 1000, "voice_xp": 2000},
        ])
    assert await repository.rank_snapshot(1, 3) == {
        "text_xp": 10, "voice_xp": 5, "text_rank": 3, "voice_rank": 2}
    assert await repository.rank_snapshot(2, 3) == {
        "text_xp": 1000, "voice_xp": 2000, "text_rank": 1, "voice_rank": 1}
    assert await repository.rank_snapshot(3, 3) == {
        "text_xp": 0, "voice_xp": 0, "text_rank": 1, "voice_rank": 1}


async def test_rank_snapshot_uses_one_read_statement_and_does_not_create_member(repository):
    # Warm the pool before observing statements (SQLite's schema guard is not a
    # rank query). A read must not insert an otherwise absent XP member.
    await repository.health()
    statements = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(repository.engine.sync_engine, "before_cursor_execute", observe)
    try:
        assert await repository.rank_snapshot(1, 2) == {
            "text_xp": 0, "voice_xp": 0, "text_rank": 1, "voice_rank": 1}
    finally:
        event.remove(repository.engine.sync_engine, "before_cursor_execute", observe)
    assert len(statements) == 1
    assert "INSERT" not in statements[0].upper()
    async with repository.engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(xp)) == 0


async def test_voice_cooldown_atomic_concurrency_and_text_independence(repository):
    results = await asyncio.gather(*(repository.award(1, 2, "voice", now=1000) for _ in range(16)))
    assert results.count(5) == 1
    assert results.count(None) == 15
    assert await repository.award(1, 2, "text", now=1000) == 5
    assert await repository.award(1, 2, "voice", now=1059.9) is None
    assert await repository.award(1, 2, "voice", now=1060) == 10
    assert (await repository.rank_snapshot(1, 2))["voice_xp"] == 10
    assert (await repository.rank_snapshot(2, 2))["voice_xp"] == 0


async def test_real_sql_voice_measurement_survives_cog_restart_without_catchup(repository):
    cog, clock, guild, room = harness(db=repository)
    await cog.poll()
    await minutes(cog, clock)
    assert (await repository.rank_snapshot(1, 2))["voice_xp"] == 5
    new_cog = VoiceXP(cog.bot, clock=clock)
    clock.value += 3600
    await new_cog.poll()
    assert (await repository.rank_snapshot(1, 2))["voice_xp"] == 5
    # The next measured minute is still checked by the persistent wall-clock
    # cooldown; simulated monotonic jumps cannot force another SQL award.
    await minutes(new_cog, clock)
    assert (await repository.rank_snapshot(1, 2))["voice_xp"] == 5


@pytest.mark.parametrize("field,value", [
    ("guild_id", 0), ("guild_id", -1), ("guild_id", True), ("guild_id", 2**63),
    ("user_id", "2"), ("user_id", 1.0), ("user_id", None),
])
async def test_rank_snapshot_rejects_invalid_scope_before_io(repository, field, value):
    values = {"guild_id": 1, "user_id": 2, field: value}
    with pytest.raises(ValueError, match="rank scope"):
        await repository.rank_snapshot(**values)
