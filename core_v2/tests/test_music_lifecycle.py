"""Adversarial command/auto-rejoin interleavings with real Cog callbacks."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core_v2.music import Music, Session, _OwnedVoiceClient
from core_v2.music_engine import failure_message
from core_v2.security import UserError
from core_v2.tests.test_music_commands import invocation as invocation


async def eventually(condition):
    async with asyncio.timeout(2):
        while not condition():
            await asyncio.sleep(.001)


async def call(fixture, command, *args):
    return await getattr(Music, command).callback(fixture.cog, fixture.interaction, *args)


async def test_stop_while_autojoin_member_lookup_is_pending_cannot_rejoin(invocation):
    f = invocation
    entered, release = asyncio.Event(), asyncio.Event()
    first = True

    async def fetch(user_id):
        nonlocal first
        if user_id == 99 and first:
            first = False
            entered.set()
            await release.wait()
        return f.actor if user_id == 20 else f.bot_member

    f.guild.fetch_member.side_effect = fetch
    f.cog.options.autojoin_channels = (30,)
    f.cog.bot.get_channel.return_value = f.channel
    auto = asyncio.create_task(f.cog.autojoin.coro(f.cog))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await call(f, "stop")
        release.set()
        await auto
        assert 10 in f.cog._stopped_guilds
        assert not f.cog.sessions
        f.channel.connect.assert_not_awaited()
        f.manager.attach.assert_not_called()
    finally:
        release.set()
        await asyncio.gather(auto, return_exceptions=True)


@pytest.mark.parametrize("automatic", [True, False], ids=["autojoin", "manual-connect"])
async def test_stop_during_connect_closes_late_owned_client_before_attach(invocation, automatic):
    f = invocation
    entered, release = asyncio.Event(), asyncio.Event()

    async def connect(**kwargs):
        entered.set()
        await release.wait()
        return f.voice

    f.channel.connect.side_effect = connect
    pending = asyncio.create_task(f.cog._ensure(f.guild, f.channel, automatic=automatic))
    stopper = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        stopper = asyncio.create_task(call(f, "stop"))
        await eventually(lambda: 10 in f.cog._stopped_guilds)
        release.set()
        with pytest.raises(UserError, match="зупинено"):
            await pending
        await stopper
        f.voice.disconnect.assert_awaited_once_with(force=True)
        f.manager.attach.assert_not_called()
        assert not f.cog.sessions and 10 in f.cog._stopped_guilds
    finally:
        release.set()
        await asyncio.gather(pending, *([stopper] if stopper else []), return_exceptions=True)


async def test_automatic_ensure_never_clears_deliberate_stop_but_new_manual_request_can(invocation):
    f = invocation
    f.cog._stopped_guilds.add(10)
    with pytest.raises(UserError, match="скасовано"):
        await f.cog._ensure(f.guild, f.channel, automatic=True)
    f.channel.connect.assert_not_awaited()
    assert 10 in f.cog._stopped_guilds
    await f.cog._ensure(f.guild, f.channel)
    assert 10 not in f.cog._stopped_guilds
    await f.cog._remove(10)


async def test_unload_rejects_late_connection_and_ready_does_not_restart_task(invocation):
    f = invocation
    entered, release = asyncio.Event(), asyncio.Event()

    async def connect(**kwargs):
        entered.set()
        await release.wait()
        return f.voice

    f.channel.connect.side_effect = connect
    pending = asyncio.create_task(f.cog._ensure(f.guild, f.channel))
    try:
        await entered.wait()
        await f.cog.cog_unload()
        release.set()
        with pytest.raises(UserError, match="зупинено"):
            await pending
        await f.cog.on_ready()
        assert not f.cog.autojoin.is_running() and not f.cog.sessions
        f.manager.attach.assert_not_called()
        f.voice.disconnect.assert_awaited_once_with(force=True)
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("query", ["file:///private", "https://localhost/audio", "https://unsupported.example/audio", "x\narg"],
                         ids=["file", "localhost", "unsupported-host", "control"])
async def test_invalid_play_query_creates_no_voice_or_autoplay_side_effect(invocation, query):
    f = invocation
    f.cog._stopped_guilds.add(10)
    with pytest.raises(UserError):
        await call(f, "play", query)
    assert 10 in f.cog._stopped_guilds
    f.channel.connect.assert_not_awaited()
    f.manager.attach.assert_not_called()
    f.player.enqueue.assert_not_awaited()
    f.guild.fetch_member.assert_not_awaited()


async def test_cancelled_remove_keeps_owned_teardown_tracked_until_disconnect(invocation):
    f = invocation
    await f.cog._ensure(f.guild, f.channel)
    entered, release = asyncio.Event(), asyncio.Event()

    async def disconnect(**kwargs):
        entered.set()
        await release.wait()
        f.voice.is_connected.return_value = False

    f.voice.disconnect.side_effect = disconnect
    removal = asyncio.create_task(f.cog._remove(10))
    try:
        await entered.wait()
        removal.cancel()
        with pytest.raises(asyncio.CancelledError):
            await removal
        assert 10 in f.cog.sessions and 10 in f.cog._teardowns
        with pytest.raises(UserError, match="завершує"):
            await f.cog._ensure(f.guild, f.channel)
        f.channel.connect.assert_awaited_once()
        release.set()
        await eventually(lambda: not f.cog._teardowns)
        assert not f.cog.sessions
        f.manager.detach.assert_awaited_once_with(10)
        f.voice.disconnect.assert_awaited_once_with(force=True)
    finally:
        release.set()
        await asyncio.gather(removal, *f.cog._teardowns.values(), return_exceptions=True)


async def test_sdk_connect_cancellation_disconnects_and_cleans_only_owned_protocol(monkeypatch):
    entered = asyncio.Event()

    async def connect(self, **kwargs):
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(discord.VoiceClient, "connect", connect)
    owned = object.__new__(_OwnedVoiceClient)
    owned.disconnect = AsyncMock()
    owned.cleanup = MagicMock()
    owned.is_connected = MagicMock(return_value=False)
    task = asyncio.create_task(owned.connect(timeout=20, reconnect=True))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    owned.disconnect.assert_awaited_once_with(force=True)
    owned.cleanup.assert_called_once()


@pytest.mark.parametrize("state,label", [("idle", "Очікування"), ("resolving", "Отримання"),
    ("playing", "Відтворення"), ("reconnecting", "Відновлення"), ("cleanup", "Завершення"),
    ("backoff", "Пауза"), ("closed", "Зупинено"), ("disconnected", "втрачено"),
    ("synthetic-private-state", "Невідомо")])
async def test_queue_displays_localized_bounded_state_without_raw_exception(invocation, state, label):
    f = invocation
    await f.cog._ensure(f.guild, f.channel)
    f.player.snapshot.return_value.update(state=state, error="private-token endpoint?credential=secret",
                                          error_code="network")
    await call(f, "queue")
    message = f.interaction.followup.send.await_args.args[0]
    assert label in message and failure_message("network") in message
    assert "private" not in message and "credential" not in message
    assert len(message.encode("utf-16-le")) // 2 < 2000
    await f.cog._remove(10)


async def test_unknown_error_code_uses_fixed_fallback(invocation):
    f = invocation
    await f.cog._ensure(f.guild, f.channel)
    f.player.snapshot.return_value.update(error="synthetic-secret", error_code={"token": "secret"})
    await call(f, "queue")
    message = f.interaction.followup.send.await_args.args[0]
    assert failure_message("source_unavailable") in message
    assert "synthetic-secret" not in message and "token" not in message
    await f.cog._remove(10)


async def test_removed_guild_blocks_old_automatic_request_before_connect(invocation):
    f = invocation
    f.cog.bot.get_guild.return_value = None
    with pytest.raises(UserError, match="Сервер"):
        await f.cog._ensure(f.guild, f.channel, automatic=True)
    f.channel.connect.assert_not_awaited()


async def test_guild_removed_during_connect_closes_owned_client_and_publishes_nothing(invocation):
    f = invocation
    entered, release = asyncio.Event(), asyncio.Event()

    async def connect(**kwargs):
        entered.set()
        await release.wait()
        return f.voice

    f.channel.connect.side_effect = connect
    pending = asyncio.create_task(f.cog._ensure(f.guild, f.channel, automatic=True))
    try:
        await entered.wait()
        f.cog.bot.get_guild.return_value = None
        release.set()
        with pytest.raises(UserError, match="скасовано"):
            await pending
        f.voice.disconnect.assert_awaited_once_with(force=True)
        f.manager.attach.assert_not_called()
        assert not f.cog.sessions
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_slow_disconnect_on_one_guild_does_not_block_another_guild(invocation):
    f = invocation
    entered, release = asyncio.Event(), asyncio.Event()

    async def close():
        entered.set()
        await release.wait()
        return True

    first, second = MagicMock(), MagicMock()
    first.close, second.close = AsyncMock(side_effect=close), AsyncMock(return_value=True)
    f.cog.sessions[10] = Session(first, f.player)
    f.cog.sessions[11] = Session(second, f.player)
    pending = asyncio.create_task(f.cog._remove(10))
    try:
        await entered.wait()
        await asyncio.wait_for(f.cog._remove(11), 1)
        assert 11 not in f.cog.sessions and 10 in f.cog.sessions
        assert not pending.done()
        second.close.assert_awaited_once()
        release.set()
        await pending
        first.close.assert_awaited_once()
        assert not f.cog.sessions and not f.cog._teardowns
    finally:
        release.set()
        await asyncio.gather(pending, *f.cog._teardowns.values(), return_exceptions=True)
