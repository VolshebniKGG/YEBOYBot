"""Rank command renders the real template offline, with bounded I/O and workers."""
import asyncio
from io import BytesIO
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from PIL import Image
import pytest

from core_v2 import commands as module
from core_v2.commands import Core
from core_v2.security import UserError


def png(colour):
    output = BytesIO()
    Image.new("RGB", (32, 32), colour).save(output, "PNG")
    return output.getvalue()


def asset(data):
    result = MagicMock(spec=discord.Asset)
    result.with_size.return_value = result
    result.with_static_format.return_value = result
    result.read = AsyncMock(return_value=data)
    return result


@pytest.fixture
def invocation():
    guild = SimpleNamespace(id=10, icon=asset(png("blue")))
    actor = MagicMock(spec=discord.Member)
    actor.id = 20
    actor.guild = guild
    actor.display_name = "Тестовий_учасник"
    actor.display_avatar = asset(png("red"))
    other = MagicMock(spec=discord.Member)
    other.id = 30
    other.guild = guild
    other.display_name = "Інший_учасник"
    other.display_avatar = asset(png("green"))
    interaction = SimpleNamespace(guild=guild, guild_id=guild.id, user=actor,
        response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
    database = SimpleNamespace(rank_snapshot=AsyncMock(return_value={"text_xp": 1339,
        "voice_xp": 86380, "text_rank": 1, "voice_rank": 2}),
        leaderboard=AsyncMock(return_value=[]))
    cog = Core(SimpleNamespace(db=database))
    return cog, interaction, other


async def invoke(fixture, member=None):
    cog, interaction, _ = fixture
    await Core.rank.callback(cog, interaction, member)


def output(interaction):
    result = interaction.followup.send.await_args.kwargs
    assert result["allowed_mentions"].to_dict()["parse"] == []
    return result


async def test_rank_uses_real_background_two_icons_and_separate_xp(invocation):
    cog, interaction, _ = invocation
    await invoke(invocation)
    interaction.response.defer.assert_awaited_once_with()
    cog.bot.db.rank_snapshot.assert_awaited_once_with(10, 20)
    result = output(interaction)
    assert "ephemeral" not in result
    assert result["file"].filename == "rank.png"
    assert result["embed"].image.url == "attachment://rank.png"
    assert "1339 XP" in result["embed"].description
    assert "86380 XP" in result["embed"].description
    image = Image.open(result["file"].fp)
    assert image.size == (500, 188)
    assert image.getpixel((78, 64))[:3] == (255, 0, 0)
    assert image.getpixel((200, 18))[:3] == (0, 0, 255)
    interaction.user.display_avatar.with_size.assert_called_once_with(256)
    interaction.user.display_avatar.with_static_format.assert_called_once_with("png")
    interaction.guild.icon.with_size.assert_called_once_with(64)


async def test_rank_selected_member_has_own_name_avatar_and_guild_scope(invocation):
    cog, interaction, other = invocation
    await invoke(invocation, other)
    cog.bot.db.rank_snapshot.assert_awaited_once_with(10, 30)
    assert discord.utils.escape_markdown(other.display_name) in output(interaction)["embed"].title
    image = Image.open(output(interaction)["file"].fp)
    assert image.getpixel((78, 64))[:3] == (0, 128, 0)
    interaction.user.display_avatar.read.assert_not_awaited()


@pytest.mark.parametrize("invalid", ["dm", "non-member", "wrong-target-guild"])
async def test_rank_rejects_invalid_scope_before_any_database_read(invocation, invalid):
    cog, interaction, other = invocation
    member = None
    if invalid == "dm":
        interaction.guild = None
    elif invalid == "non-member":
        interaction.user = SimpleNamespace(id=20)
    else:
        other.guild = SimpleNamespace(id=999)
        member = other
    with pytest.raises(UserError):
        await invoke(invocation, member)
    cog.bot.db.rank_snapshot.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize("error", [RuntimeError("private-db-url"), TimeoutError("private-db-url")])
async def test_rank_database_failure_is_private_and_does_not_fetch_icons(invocation, error, caplog):
    cog, interaction, _ = invocation
    cog.bot.db.rank_snapshot.side_effect = error
    await invoke(invocation)
    result = output(interaction)
    assert result["ephemeral"] is True
    assert "file" not in result
    assert "private-db-url" not in str(result) + caplog.text
    interaction.user.display_avatar.read.assert_not_awaited()


async def test_rank_real_database_timeout_and_cancellation_are_distinct(invocation, monkeypatch):
    cog, interaction, _ = invocation
    cancelled = asyncio.Event()

    async def blocked(*args):
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    cog.bot.db.rank_snapshot.side_effect = blocked
    monkeypatch.setattr(module, "RANK_DB_TIMEOUT", .01)
    await invoke(invocation)
    assert cancelled.is_set()
    assert output(interaction)["ephemeral"] is True
    interaction.followup.send.reset_mock()
    monkeypatch.setattr(module, "RANK_DB_TIMEOUT", 100)
    task = asyncio.create_task(invoke(invocation))
    await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize("failure", ["missing-guild-icon", "http-failure", "invalid-image", "oversized", "timeout"])
async def test_rank_icon_failure_still_renders_card(invocation, failure, monkeypatch):
    _, interaction, _ = invocation
    if failure == "missing-guild-icon":
        interaction.guild.icon = None
    elif failure == "http-failure":
        interaction.user.display_avatar.read.side_effect = OSError("network detail")
    elif failure == "invalid-image":
        interaction.user.display_avatar.read.return_value = b"not-image"
    elif failure == "oversized":
        interaction.user.display_avatar.read.return_value = b"x" * (module.RANK_ASSET_LIMIT + 1)
    else:
        async def forever():
            await asyncio.Future()
        interaction.user.display_avatar.read.side_effect = forever
        monkeypatch.setattr(module, "RANK_ASSET_TIMEOUT", .01)
    await invoke(invocation)
    image = Image.open(output(interaction)["file"].fp)
    assert image.size == (500, 188)


async def test_rank_fetches_two_assets_concurrently(invocation):
    _, interaction, _ = invocation
    started = set()
    both = asyncio.Event()

    async def read(which):
        started.add(which)
        if len(started) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), .5)
        return png("red")

    async def avatar_read():
        return await read("avatar")
    async def icon_read():
        return await read("icon")
    interaction.user.display_avatar.read.side_effect = avatar_read
    interaction.guild.icon.read.side_effect = icon_read
    await invoke(invocation)
    assert started == {"avatar", "icon"}
    assert "file" in output(interaction)


async def test_rank_renders_off_event_loop(invocation, monkeypatch):
    thread_id = threading.get_ident()
    original = module.render_rank_card
    workers = []

    def render(**values):
        workers.append(threading.get_ident())
        return original(**values)

    monkeypatch.setattr(module, "render_rank_card", render)
    await invoke(invocation)
    assert workers and workers[0] != thread_id


@pytest.mark.parametrize("failure", ["exception", "oversized", "wrong-type"])
async def test_rank_render_failure_is_private(invocation, monkeypatch, failure, caplog):
    _, interaction, _ = invocation
    def render(**values):
        if failure == "exception":
            raise RuntimeError("secret-render-path")
        return b"x" * (module.RANK_PNG_LIMIT + 1) if failure == "oversized" else None
    monkeypatch.setattr(module, "render_rank_card", render)
    await invoke(invocation)
    assert output(interaction)["ephemeral"] is True
    assert "secret-render-path" not in caplog.text


async def test_rank_empty_xp_long_unicode_name_and_mentions_are_safe(invocation):
    cog, interaction, _ = invocation
    interaction.user.display_name = "@everyone **Ім'я🦊**" * 200
    cog.bot.db.rank_snapshot.return_value = {"text_xp": 0, "voice_xp": 0,
        "text_rank": None, "voice_rank": None}
    await invoke(invocation)
    result = output(interaction)
    assert len(result["embed"].title) <= 206
    assert "@everyone" not in result["embed"].title
    assert "місце —" in result["embed"].description
    assert len(result["file"].fp.getvalue()) <= module.RANK_PNG_LIMIT


async def test_rank_lost_followup_never_requeries_or_rerenders(invocation, monkeypatch):
    cog, interaction, _ = invocation
    render = MagicMock(wraps=module.render_rank_card)
    monkeypatch.setattr(module, "render_rank_card", render)
    interaction.followup.send.side_effect = OSError("lost response")
    with pytest.raises(OSError):
        await invoke(invocation)
    cog.bot.db.rank_snapshot.assert_awaited_once()
    assert render.call_count == 1


async def test_render_timeout_holds_permit_until_actual_thread_finishes(invocation, monkeypatch):
    cog, _, _ = invocation
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def render(**values):
        nonlocal calls
        calls += 1
        started.set()
        release.wait(2)
        return b"png"

    monkeypatch.setattr(module, "render_rank_card", render)
    monkeypatch.setattr(module, "RANK_RENDER_TIMEOUT", .01)
    monkeypatch.setattr(module, "RANK_SLOT_TIMEOUT", .01)
    cog._rank_slots = asyncio.Semaphore(1)
    try:
        with pytest.raises(TimeoutError):
            await cog._render_rank()
        assert started.is_set()
        assert cog._rank_slots.locked()
        assert len(cog._rank_workers) == 1
        with pytest.raises(TimeoutError):
            await cog._render_rank()
        assert calls == 1
    finally:
        release.set()
        await asyncio.gather(*cog._rank_workers)
        await asyncio.sleep(0)
    assert not cog._rank_slots.locked()
    assert not cog._rank_workers


async def test_cancelled_render_waiter_holds_permit_until_worker_exits(invocation, monkeypatch):
    cog, _, _ = invocation
    started = threading.Event()
    release = threading.Event()
    def render(**values):
        started.set()
        release.wait(2)
        raise ValueError("worker error after caller left")
    monkeypatch.setattr(module, "render_rank_card", render)
    cog._rank_slots = asyncio.Semaphore(1)
    task = asyncio.create_task(cog._render_rank())
    try:
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cog._rank_slots.locked()
    finally:
        release.set()
        await asyncio.gather(*cog._rank_workers, return_exceptions=True)
        await asyncio.sleep(0)
    assert not cog._rank_slots.locked()
    assert not cog._rank_workers


async def test_leaderboard_combines_counters_without_mentions(invocation):
    cog, interaction, _ = invocation
    cog.bot.db.leaderboard.return_value = [{"user_id": 30, "text_xp": 10, "voice_xp": 20}]
    await Core.leaderboard.callback(cog, interaction, 2)
    cog.bot.db.leaderboard.assert_awaited_once_with(10, 2)
    result = output(interaction)
    assert "чат + голос" in result["embed"].title
    assert "11. <@30> — 30 XP" in result["embed"].description


async def test_rank_asset_cancellation_propagates_without_sending_card(invocation):
    _, interaction, _ = invocation
    entered = asyncio.Event()
    async def waiting():
        entered.set()
        await asyncio.Future()
    interaction.user.display_avatar.read.side_effect = waiting
    task = asyncio.create_task(invoke(invocation))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    interaction.followup.send.assert_not_awaited()


async def test_rank_busy_renderer_reports_private_error_without_queuing_thread(invocation, monkeypatch):
    cog, interaction, _ = invocation
    cog._rank_slots = asyncio.Semaphore(0)
    monkeypatch.setattr(module, "RANK_SLOT_TIMEOUT", .01)
    render = MagicMock(side_effect=AssertionError("No work without permit"))
    monkeypatch.setattr(module, "render_rank_card", render)
    await invoke(invocation)
    assert output(interaction)["ephemeral"] is True
    assert not cog._rank_workers
    render.assert_not_called()


async def test_rank_real_sqlite_snapshot_render_is_read_only_and_guild_scoped(invocation, tmp_path):
    from core_v2.db import Database
    cog, interaction, _ = invocation
    database = Database("sqlite+aiosqlite:///" + str(tmp_path / "картка рангу.db"))
    await database.initialize()
    try:
        await database.award(10, 20, "text", now=1000)
        await database.award(10, 30, "text", now=1000)
        await database.award(10, 20, "voice", now=1000)
        await database.award(10, 20, "voice", now=1060)
        await database.award(999, 20, "voice", now=1000)
        cog.bot.db = database
        before = await database.rank(10, 20)
        await invoke(invocation)
        result = output(interaction)
        assert result["embed"].description == "Чат: 5 XP · місце 1\nГолос: 10 XP · місце 1"
        assert Image.open(result["file"].fp).size == (500, 188)
        assert await database.rank(10, 20) == before
        assert (await database.rank(999, 20))["voice_xp"] == 5
    finally:
        await database.close()


async def test_rank_embed_name_strips_controls_surrogates_and_fits_utf16_limit(invocation):
    _, interaction, _ = invocation
    interaction.user.display_name = "\u202e\ud800\x00" + "🦊" * 500
    await invoke(invocation)
    name = output(interaction)["embed"].title
    assert "\u202e" not in name and "\ud800" not in name and "\x00" not in name
    assert len(name.encode("utf-16-le")) // 2 < 256
