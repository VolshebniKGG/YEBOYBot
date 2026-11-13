"""Owned voice/audio teardown failures never authorize a second connection."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core_v2 import music as module
from core_v2.music import CLOSE_PENDING, Session
from core_v2.security import UserError
from core_v2.tests.test_music_commands import (backend as backend, invocation as invocation,
                                                invoke, output, track)


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


@pytest.mark.parametrize("state", [True, None, 0, 1, "false"],
                         ids=["connected", "unknown", "integer-zero", "integer-one", "string"])
async def test_backend_close_requires_strict_false_transport_state(backend, state):
    backend.voice.disconnect.side_effect = None
    backend.voice.is_connected.return_value = state
    assert await backend.backend.close() is False
    backend.voice.disconnect.assert_awaited_once_with(force=True)


async def test_backend_close_confirms_completed_source_and_disconnected_transport(backend):
    backend.backend.play(track(), MagicMock())
    assert await backend.backend.close() is True
    backend.wrapped.cleanup.assert_called_once()
    assert backend.voice.is_connected() is False


@pytest.mark.parametrize("cleaned", [False, None, 0, 1], ids=["false", "unknown", "zero", "one"])
async def test_backend_close_checks_strict_cleanup_result_but_still_disconnects(backend, cleaned):
    backend.backend.wait_stopped = AsyncMock(return_value=cleaned)
    assert await backend.backend.close() is False
    assert backend.voice.is_connected() is False


async def test_disconnect_timeout_keeps_result_unconfirmed(backend, monkeypatch, caplog):
    monkeypatch.setattr(module, "VOICE_DISCONNECT_TIMEOUT", .01)

    async def stuck(**kwargs):
        await asyncio.Future()

    backend.voice.disconnect.side_effect = stuck
    assert await backend.backend.close() is False
    assert "TimeoutError" in caplog.text


async def test_swallowed_disconnect_deadline_cannot_claim_success(backend, monkeypatch):
    monkeypatch.setattr(module, "VOICE_DISCONNECT_TIMEOUT", .01)

    async def swallow_cancel(**kwargs):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            backend.voice.is_connected.return_value = False

    backend.voice.disconnect.side_effect = swallow_cancel
    assert await backend.backend.close() is False
    assert backend.voice.is_connected() is False


async def test_swallowed_cleanup_deadline_cannot_claim_success(backend, monkeypatch):
    monkeypatch.setattr(module, "AUDIO_CLEANUP_TIMEOUT", .01)

    async def swallow_cancel(**kwargs):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            return True

    backend.backend.wait_stopped = swallow_cancel
    assert await backend.backend.close() is False
    backend.voice.disconnect.assert_awaited_once()


async def test_source_cleanup_exception_stays_owned_and_cannot_spawn_replacement(backend, caplog):
    backend.wrapped.cleanup.side_effect = RuntimeError("synthetic-private-credential")
    backend.backend.play(track(), MagicMock())
    owned = backend.backend._source
    assert await backend.backend.close() is False
    assert backend.backend._source is owned and owned.cleanup_failed is True
    assert await backend.backend.wait_stopped() is False
    assert "RuntimeError" in caplog.text and "synthetic-private" not in caplog.text
    # Reconnect cannot replace an audio source whose native cleanup failed.
    backend.voice.is_connected.return_value = True
    with pytest.raises(module.MusicError, match="завершується"):
        backend.backend.play(track(), MagicMock())
    backend.factory.assert_called_once()
    assert await backend.backend.close() is False
    backend.wrapped.cleanup.assert_called_once()


async def test_failed_disconnect_quarantines_same_session_and_manual_stop_retries(invocation, caplog):
    f = invocation
    session = await f.cog._ensure(f.guild, f.channel)
    f.voice.disconnect.side_effect = RuntimeError("synthetic-private-endpoint")
    await invoke(f, "stop")
    assert output(f) == CLOSE_PENDING
    assert f.cog.sessions[10] is session
    assert session.closing and session.close_failed and not f.cog._teardowns
    assert "synthetic-private" not in caplog.text
    with pytest.raises(UserError, match="підтверджено"):
        await f.cog._ensure(f.guild, f.channel)
    f.channel.connect.assert_awaited_once()

    async def disconnected(**kwargs):
        f.voice.is_connected.return_value = False

    f.voice.disconnect.side_effect = disconnected
    await invoke(f, "stop")
    assert "очищено" in output(f) and not f.cog.sessions
    assert 10 in f.cog._stopped_guilds
    assert f.voice.disconnect.await_count == 2
    f.channel.connect.assert_awaited_once()


@pytest.mark.parametrize("command,args", [("play", ("музика",)), ("join", ()),
                                          ("skip", ()), ("autoplay", (True,))],
                         ids=["play", "join", "skip", "autoplay"])
async def test_quarantined_session_rejects_mutating_commands(invocation, command, args):
    f = invocation
    session = await f.cog._ensure(f.guild, f.channel)
    session.closing = session.close_failed = True
    with pytest.raises(UserError, match="завершує"):
        await invoke(f, command, *args)
    f.channel.connect.assert_awaited_once()
    f.player.enqueue.assert_not_awaited()
    f.player.skip.assert_not_awaited()
    f.player.set_autoplay.assert_not_awaited()
    assert await f.cog._remove(10) is True


@pytest.mark.parametrize("failed", [False, True], ids=["closing", "failed"])
async def test_queue_shows_cleanup_state_without_reading_dead_player(invocation, failed):
    f = invocation
    session = await f.cog._ensure(f.guild, f.channel)
    session.closing, session.close_failed = True, failed
    session.player = None
    await invoke(f, "queue")
    assert "заблоковано" in output(f)
    assert ("Не підтверджено" in output(f)) is failed
    assert await f.cog._remove(10) is True


async def test_two_stop_calls_share_single_active_owned_teardown(invocation):
    f = invocation
    await f.cog._ensure(f.guild, f.channel)
    entered, release = asyncio.Event(), asyncio.Event()

    async def disconnect(**kwargs):
        entered.set()
        await release.wait()
        f.voice.is_connected.return_value = False

    f.voice.disconnect.side_effect = disconnect
    first = asyncio.create_task(f.cog._remove(10))
    try:
        await entered.wait()
        second = asyncio.create_task(f.cog._remove(10))
        await asyncio.sleep(0)
        assert not second.done()
        f.voice.disconnect.assert_awaited_once()
        f.manager.detach.assert_awaited_once_with(10)
        release.set()
        assert await asyncio.gather(first, second) == [True, True]
        assert not f.cog.sessions and not f.cog._teardowns
    finally:
        release.set()
        await asyncio.gather(first, *f.cog._teardowns.values(), return_exceptions=True)


async def test_cancelled_failed_teardown_keeps_session_for_manual_retry(invocation):
    f = invocation
    session = await f.cog._ensure(f.guild, f.channel)
    entered, release = asyncio.Event(), asyncio.Event()

    async def disconnect(**kwargs):
        entered.set()
        await release.wait()
        raise RuntimeError("synthetic-private-value")

    f.voice.disconnect.side_effect = disconnect
    removal = asyncio.create_task(f.cog._remove(10))
    try:
        await entered.wait()
        removal.cancel()
        with pytest.raises(asyncio.CancelledError):
            await removal
        assert f.cog.sessions[10] is session and 10 in f.cog._teardowns
        release.set()
        await eventually(lambda: not f.cog._teardowns)
        assert f.cog.sessions[10] is session and session.close_failed
    finally:
        release.set()
        await asyncio.gather(removal, *f.cog._teardowns.values(), return_exceptions=True)


async def test_detach_failure_does_not_discard_worker_and_retry_closes_original(invocation):
    f = invocation
    session = await f.cog._ensure(f.guild, f.channel)
    f.player.close = AsyncMock()
    f.manager.detach.side_effect = RuntimeError("synthetic-private-worker")
    assert await f.cog._remove(10) is False
    assert f.cog.sessions[10] is session and session.detach_failed
    f.voice.disconnect.assert_awaited_once()
    f.manager.detach.side_effect = None
    assert await f.cog._remove(10) is True
    f.player.close.assert_awaited_once()
    assert f.manager.detach.await_count == 2 and not f.cog.sessions


async def test_swallowed_detach_deadline_retains_original_worker(invocation, monkeypatch):
    f = invocation
    session = await f.cog._ensure(f.guild, f.channel)
    monkeypatch.setattr(module, "PLAYER_CLOSE_TIMEOUT", .01)

    async def swallow_cancel(guild_id):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            return

    f.manager.detach.side_effect = swallow_cancel
    assert await f.cog._remove(10) is False
    assert session.detach_failed and f.cog.sessions[10] is session


@pytest.mark.parametrize("reason", ["stopped", "removed", "attach"], ids=["stop-race", "guild-race", "attach-failure"])
async def test_late_connection_cleanup_failure_is_owned_and_blocks_second_connect(invocation, reason):
    f = invocation

    async def connect(**kwargs):
        if reason == "stopped":
            f.cog._stopped_guilds.add(10)
        elif reason == "removed":
            f.cog.bot.get_guild.return_value = None
        return f.voice

    f.channel.connect.side_effect = connect
    f.voice.disconnect.side_effect = RuntimeError("synthetic-private-value")
    if reason == "attach":
        f.manager.attach.side_effect = UserError("Приєднання відхилено.")
    with pytest.raises(UserError):
        await f.cog._ensure(f.guild, f.channel)
    session = f.cog.sessions[10]
    assert session.backend.voice is f.voice and session.player is None
    assert session.closing and session.close_failed
    assert not f.cog._teardowns
    f.cog.bot.get_guild.return_value = f.guild
    with pytest.raises(UserError, match="підтверджено"):
        await f.cog._ensure(f.guild, f.channel)
    f.channel.connect.assert_awaited_once()


async def test_stop_can_remove_owned_client_after_bot_channel_permissions_revoked(invocation):
    f = invocation
    await f.cog._ensure(f.guild, f.channel)
    actor_permissions = discord.Permissions(view_channel=True, connect=True, speak=True)
    f.channel.permissions_for.side_effect = lambda member: (
        actor_permissions if member.id == 20 else discord.Permissions.none())
    await invoke(f, "stop")
    assert "очищено" in output(f) and not f.cog.sessions
    f.voice.disconnect.assert_awaited_once()


async def test_stop_retry_uses_owned_channel_id_when_voice_channel_is_none(invocation):
    f = invocation
    session = await f.cog._ensure(f.guild, f.channel)
    session.closing = session.close_failed = True
    f.voice.channel = None
    await invoke(f, "stop")
    assert "очищено" in output(f) and not f.cog.sessions


async def test_stop_retry_still_rejects_foreign_channel_and_non_manager(invocation):
    f = invocation
    session = await f.cog._ensure(f.guild, f.channel)
    session.closing = session.close_failed = True
    moved_channel = MagicMock(spec=discord.VoiceChannel)
    moved_channel.id, moved_channel.guild = 31, f.guild
    f.voice.channel = moved_channel
    with pytest.raises(UserError, match="того самого"):
        await invoke(f, "stop")
    f.voice.channel = f.channel
    f.actor.guild_permissions = discord.Permissions.none()
    with pytest.raises(UserError, match="Керувати сервером"):
        await invoke(f, "stop")
    f.voice.disconnect.assert_not_awaited()
    assert await f.cog._remove(10) is True


async def test_sdk_connectable_cancel_captures_constructed_client_before_return(invocation, monkeypatch):
    """Use actual Connectable.connect constructor/cache/cancellation path."""
    f = invocation
    entered = asyncio.Event()
    registered = {}

    def initialize(self, client, channel):
        self.client, self.channel = client, channel
        self.test_connected = True

    async def connect(self, **kwargs):
        entered.set()
        await asyncio.Future()

    disconnected = []

    async def disconnect(self, **kwargs):
        disconnected.append(self)
        raise RuntimeError("synthetic-private-handshake")

    monkeypatch.setattr(discord.VoiceClient, "__init__", initialize)
    monkeypatch.setattr(discord.VoiceClient, "connect", connect)
    monkeypatch.setattr(discord.VoiceClient, "disconnect", disconnect)
    monkeypatch.setattr(discord.VoiceClient, "stop", lambda self: None)
    monkeypatch.setattr(discord.VoiceClient, "is_connected", lambda self: self.test_connected)
    f.channel._get_voice_client_key.return_value = (10, "guild_id")
    f.channel._state = SimpleNamespace(
        _get_voice_client=registered.get, _get_client=lambda: f.cog.bot,
        _add_voice_client=lambda key, value: registered.__setitem__(key, value))
    async def channel_connect(**kwargs):
        return await discord.abc.Connectable.connect(f.channel, **kwargs)

    f.channel.connect.side_effect = channel_connect
    pending = asyncio.create_task(f.cog._ensure(f.guild, f.channel))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert 10 in registered and 10 not in f.cog.sessions
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        session = f.cog.sessions[10]
        assert session.backend.voice is registered[10]
        assert session.close_failed and session.closing
        assert disconnected == [registered[10]]
        assert not f.cog._teardowns
        f.manager.attach.assert_not_called()
        with pytest.raises(UserError, match="підтверджено"):
            await f.cog._ensure(f.guild, f.channel)
        f.channel.connect.assert_awaited_once()

        async def disconnected_now(self, **kwargs):
            disconnected.append(self)
            self.test_connected = False
            assert registered.pop(10) is self

        monkeypatch.setattr(discord.VoiceClient, "disconnect", disconnected_now)
        owned = registered[10]
        assert await f.cog._remove(10) is True
        assert disconnected == [owned, owned]
        assert not registered and not f.cog.sessions and not f.cog._teardowns
        f.channel.connect.assert_awaited_once()
    finally:
        await asyncio.gather(pending, *f.cog._teardowns.values(), return_exceptions=True)


async def test_quarantined_session_counts_toward_global_player_limit(invocation):
    f = invocation
    f.cog.options.max_players = 1
    f.cog.sessions[11] = Session(MagicMock(), None, closing=True, close_failed=True)
    with pytest.raises(UserError, match="ліміт"):
        await f.cog._ensure(f.guild, f.channel)
    f.channel.connect.assert_not_awaited()


async def test_unload_reports_unconfirmed_owned_session_without_raw_details(invocation, caplog):
    f = invocation
    await f.cog._ensure(f.guild, f.channel)
    f.voice.disconnect.side_effect = RuntimeError("synthetic-private-value")
    await f.cog.cog_unload()
    assert 10 in f.cog.sessions and f.cog.sessions[10].close_failed
    assert "shutdown unconfirmed guild=10" in caplog.text
    assert "synthetic-private" not in caplog.text
    f.manager.close.assert_awaited_once()


@pytest.mark.parametrize("command", ["queue", "stop"], ids=["queue", "stop"])
async def test_external_voice_move_uses_current_channel_for_controls(invocation, command):
    f = invocation
    session = await f.cog._ensure(f.guild, f.channel)
    moved_channel = MagicMock(spec=discord.VoiceChannel)
    moved_channel.id, moved_channel.guild = 31, f.guild
    moved_channel.permissions_for.return_value = discord.Permissions(view_channel=True, connect=True, speak=True)
    f.voice.channel = moved_channel
    assert session.channel_id == 30
    with pytest.raises(UserError, match="того самого"):
        await invoke(f, command)
    f.actor.voice.channel = moved_channel
    await invoke(f, command)
    assert output(f)
    if command == "queue":
        assert await f.cog._remove(10) is True
    assert not f.cog.sessions


async def test_external_voice_move_checks_current_channel_before_play_enqueue(invocation):
    f = invocation
    await f.cog._ensure(f.guild, f.channel)
    moved_channel = MagicMock(spec=discord.VoiceChannel)
    moved_channel.id, moved_channel.guild = 31, f.guild
    moved_channel.permissions_for.return_value = discord.Permissions(view_channel=True, connect=True, speak=True)
    f.voice.channel = moved_channel
    with pytest.raises(UserError, match="того самого"):
        await invoke(f, "play", "музика")
    f.player.enqueue.assert_not_awaited()
    f.actor.voice.channel = moved_channel
    await invoke(f, "play", "музика")
    f.player.enqueue.assert_awaited_once()
    f.channel.connect.assert_awaited_once()
    assert await f.cog._remove(10) is True


async def test_real_manager_pop_before_player_close_failure_retries_exact_worker(invocation):
    f = invocation
    manager = module.MusicManager(module.load_music_options(module.ROOT), [], resolver=AsyncMock())
    f.cog.manager = manager
    session = await f.cog._ensure(f.guild, f.channel)
    player = session.player
    real_close = player.close
    attempts = 0

    async def interrupted_close():
        nonlocal attempts
        attempts += 1
        await real_close()
        if attempts == 1:
            raise RuntimeError("synthetic-private-detach")

    player.close = interrupted_close
    try:
        assert await f.cog._remove(10) is False
        assert 10 not in manager.players and f.cog.sessions[10] is session
        assert session.detach_failed and player._task.done()
        assert await f.cog._remove(10) is True
        assert attempts == 2 and not manager.players and not f.cog.sessions
        assert player._task.done()
    finally:
        await real_close()
        await manager.close()
