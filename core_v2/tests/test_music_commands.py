"""Real music slash callbacks and FFmpeg boundaries; no Discord login/audio network."""
import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core_v2 import music as module
from core_v2.music import FFmpegVoiceBackend, Music, Session
from core_v2.music_engine import MusicError
from core_v2.security import UserError


@pytest.fixture
def invocation():
    guild = SimpleNamespace(id=10, afk_channel=None, voice_client=None, unavailable=False)
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id, channel.guild = 30, guild
    actor = MagicMock(spec=discord.Member)
    actor.id, actor.guild, actor.bot = 20, guild, False
    actor.guild_permissions = discord.Permissions(manage_guild=True)
    actor.voice = SimpleNamespace(channel=channel)
    bot_member = MagicMock(spec=discord.Member)
    bot_member.id, bot_member.guild, bot_member.bot = 99, guild, True
    bot_member.guild_permissions = discord.Permissions(manage_guild=True)
    channel.permissions_for.return_value = discord.Permissions(view_channel=True, connect=True, speak=True)
    channel.members = [actor, bot_member]
    guild.fetch_member = AsyncMock(side_effect=lambda user_id: actor if user_id == 20 else bot_member)
    voice = MagicMock()
    voice.channel = channel
    voice.is_connected.return_value = True
    voice.is_playing.return_value = False
    voice.is_paused.return_value = False
    voice.disconnect = AsyncMock()
    async def disconnect(**kwargs):
        voice.is_connected.return_value = False

    async def connect(**kwargs):
        voice.is_connected.return_value = True
        return voice

    voice.disconnect.side_effect = disconnect
    channel.connect = AsyncMock(side_effect=connect)
    player = SimpleNamespace(enqueue=AsyncMock(return_value=1), skip=AsyncMock(return_value=True),
        set_autoplay=AsyncMock(), snapshot=MagicMock(return_value={
            "current": None, "request": None, "queue": (), "state": "idle", "autoplay": False,
            "autoplay_enabled": True, "error": None}))
    manager = SimpleNamespace(attach=MagicMock(return_value=player), close=AsyncMock(), detach=AsyncMock())
    options = SimpleNamespace(default_volume=.25, max_players=20, auto_pause=True,
                              self_deafen=True, autojoin_channels=())
    bot = SimpleNamespace(user=SimpleNamespace(id=99), settings=SimpleNamespace(ffmpeg="ffmpeg"),
                          wait_until_ready=AsyncMock(), get_channel=MagicMock(),
                          is_ready=MagicMock(return_value=False), get_guild=MagicMock(return_value=guild))
    cog = Music(bot, options=options, manager=manager)
    interaction = SimpleNamespace(guild=guild, user=actor, response=SimpleNamespace(defer=AsyncMock()),
                                  followup=SimpleNamespace(send=AsyncMock()))
    return SimpleNamespace(cog=cog, interaction=interaction, actor=actor, bot_member=bot_member,
                           guild=guild, channel=channel, player=player, manager=manager, voice=voice)


def output(fixture):
    call = fixture.interaction.followup.send.await_args
    assert call.kwargs["ephemeral"] is True
    assert call.kwargs["allowed_mentions"].to_dict()["parse"] == []
    return call.args[0]


async def invoke(fixture, name, *args):
    await getattr(Music, name).callback(fixture.cog, fixture.interaction, *args)


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


async def test_play_connects_one_owned_voiceclient_and_enqueues_requester(invocation):
    fixture = invocation
    await invoke(fixture, "play", "https://www.youtube.com/watch?v=abcdefghijk")
    fixture.interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    assert [call.args[0] for call in fixture.guild.fetch_member.await_args_list] == [20, 99]
    fixture.channel.connect.assert_awaited_once_with(timeout=20, reconnect=True, self_deaf=True,
                                                     cls=fixture.channel.connect.await_args.kwargs["cls"])
    factory = fixture.channel.connect.await_args.kwargs["cls"]
    assert factory.func is module._OwnedVoiceClient
    assert set(factory.keywords) == {"on_created"}
    assert callable(factory.keywords["on_created"])
    fixture.player.enqueue.assert_awaited_once_with("https://www.youtube.com/watch?v=abcdefghijk", 20)
    assert fixture.manager.attach.call_args.args[0] == 10
    assert "додано" in output(fixture)
    fixture.cog.sessions[10].backend.stop()


async def test_two_concurrent_ensures_connect_only_once(invocation):
    fixture = invocation
    first, second = await asyncio.gather(fixture.cog._ensure(fixture.guild, fixture.channel),
                                         fixture.cog._ensure(fixture.guild, fixture.channel))
    assert first is second
    fixture.channel.connect.assert_awaited_once()
    fixture.manager.attach.assert_called_once()


async def test_spotify_request_explains_public_audio_mapping(invocation):
    await invoke(invocation, "play", "spotify:track:1234567890123456789012")
    assert "метадані" in output(invocation) and "YouTube" in output(invocation)


async def test_real_manager_uses_resolved_ffmpeg_and_spotify_metadata(invocation, monkeypatch, tmp_path):
    executable = str(tmp_path / "Каталог з пробілами" / "ffmpeg.exe")
    metadata = object()
    locate = MagicMock(return_value=executable)
    credentials = MagicMock(return_value=metadata)
    monkeypatch.setattr(module, "ffmpeg_path", locate)
    monkeypatch.setattr(module.SpotifyMetadata, "from_root", credentials)
    monkeypatch.setattr(module, "load_autoplaylist", lambda root: [])
    cog = Music(invocation.cog.bot, options=module.load_music_options(module.ROOT), root=tmp_path)
    try:
        assert cog.manager.resolver.spotify is metadata
        session = await cog._ensure(invocation.guild, invocation.channel)
        assert session.backend.executable == executable
        locate.assert_called_once_with("ffmpeg", tmp_path)
        credentials.assert_called_once_with(tmp_path)
    finally:
        await cog.cog_unload()


async def test_autojoin_starts_once_after_ready_and_unload_reaps_task(invocation):
    cog = invocation.cog
    await cog.cog_load()
    assert not cog.autojoin.is_running()
    await cog.on_ready()
    task = cog.autojoin.get_task()
    assert task is not None
    await cog.on_ready()
    assert cog.autojoin.get_task() is task
    await cog.cog_unload()
    assert task.done()


async def test_cog_loaded_after_login_starts_autojoin(invocation):
    invocation.cog.bot.is_ready.return_value = True
    await invocation.cog.cog_load()
    task = invocation.cog.autojoin.get_task()
    assert task is not None
    await invocation.cog.cog_unload()
    assert task.done()


@pytest.mark.parametrize("command,args", [("play", ("музика",)), ("join", ()), ("queue", ()),
                                           ("skip", ()), ("stop", ()), ("autoplay", (True,))])
async def test_all_commands_reject_dm_before_defer_or_connect(invocation, command, args):
    invocation.interaction.guild = None
    with pytest.raises(UserError, match="сервері"):
        await invoke(invocation, command, *args)
    invocation.interaction.response.defer.assert_not_awaited()
    invocation.channel.connect.assert_not_awaited()


@pytest.mark.parametrize("command,args", [("join", ()), ("stop", ()), ("autoplay", (True,))])
async def test_manager_operations_require_current_actor_manage_guild(invocation, command, args):
    invocation.actor.guild_permissions = discord.Permissions.none()
    with pytest.raises(UserError, match="Керувати сервером"):
        await invoke(invocation, command, *args)
    invocation.channel.connect.assert_not_awaited()
    invocation.manager.detach.assert_not_awaited()


@pytest.mark.parametrize("change", ["disconnected", "stage", "afk", "cross-guild", "actor-id", "bot-id", "bot-actor"])
async def test_invalid_membership_or_voice_context_denied(invocation, change):
    fixture = invocation
    if change == "disconnected":
        fixture.actor.voice = None
    elif change == "stage":
        fixture.actor.voice.channel = MagicMock(spec=discord.StageChannel)
    elif change == "afk":
        fixture.guild.afk_channel = fixture.channel
    elif change == "cross-guild":
        fixture.actor.guild = SimpleNamespace(id=11)
    elif change == "actor-id":
        fixture.guild.fetch_member.side_effect = lambda user_id: SimpleNamespace(
            id=999, guild=fixture.guild, bot=False) if user_id == 20 else fixture.bot_member
    elif change == "bot-id":
        fixture.bot_member.id = 100
    else:
        fixture.actor.bot = True
    with pytest.raises(UserError):
        await invoke(fixture, "play", "пісня")
    fixture.channel.connect.assert_not_awaited()


@pytest.mark.parametrize("missing", ["view_channel", "connect", "speak"])
async def test_both_channel_permissions_are_required(invocation, missing):
    permissions = discord.Permissions(view_channel=True, connect=True, speak=True)
    setattr(permissions, missing, False)
    invocation.channel.permissions_for.side_effect = lambda member: (
        permissions if member.id == 99 else discord.Permissions(view_channel=True, connect=True, speak=True))
    with pytest.raises(UserError, match="Підключатися"):
        await invoke(invocation, "play", "пісня")
    invocation.channel.connect.assert_not_awaited()


async def test_different_channel_cannot_control_or_move_existing_session(invocation):
    fixture = invocation
    backend = MagicMock()
    backend.voice.channel.id = 31
    fixture.cog.sessions[10] = Session(backend, fixture.player)
    with pytest.raises(UserError, match="того самого"):
        await invoke(fixture, "skip")
    fixture.player.skip.assert_not_awaited()
    fixture.channel.connect.assert_not_awaited()


async def test_existing_foreign_voiceclient_is_not_stopped_or_replaced(invocation):
    invocation.guild.voice_client = MagicMock()
    with pytest.raises(UserError, match="інше голосове"):
        await invoke(invocation, "play", "пісня")
    invocation.guild.voice_client.stop.assert_not_called()
    invocation.channel.connect.assert_not_awaited()


async def test_player_limit_denies_connect(invocation):
    invocation.cog.options.max_players = 1
    invocation.cog.sessions[55] = object()
    with pytest.raises(UserError, match="ліміт"):
        await invoke(invocation, "play", "пісня")
    invocation.channel.connect.assert_not_awaited()


async def test_attach_failure_disconnects_owned_client(invocation):
    invocation.manager.attach.side_effect = RuntimeError("private internal error")
    with pytest.raises(RuntimeError):
        await invoke(invocation, "play", "пісня")
    invocation.voice.disconnect.assert_awaited_once_with(force=True)
    assert invocation.cog.sessions == {}


async def test_connect_cancellation_propagates_without_enqueue(invocation):
    invocation.channel.connect.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await invoke(invocation, "play", "пісня")
    invocation.player.enqueue.assert_not_awaited()
    assert invocation.cog.sessions == {}


async def test_connect_timeout_uses_private_controlled_error(invocation):
    invocation.channel.connect.side_effect = TimeoutError("private endpoint")
    with pytest.raises(UserError, match="вчасно") as error:
        await invoke(invocation, "play", "пісня")
    assert "private" not in str(error.value)


async def test_fetch_failure_does_not_connect_or_leak_error(invocation):
    invocation.guild.fetch_member.side_effect = TimeoutError("secretURL")
    with pytest.raises(UserError) as error:
        await invoke(invocation, "play", "пісня")
    assert "secretURL" not in str(error.value)
    invocation.channel.connect.assert_not_awaited()


async def test_queue_is_private_escaped_and_does_not_echo_pending_source(invocation):
    fixture = invocation
    await fixture.cog._ensure(fixture.guild, fixture.channel)
    fixture.player.snapshot.return_value.update(
        current=SimpleNamespace(title="@everyone **Улюблений** " + "😀" * 4000),
        queue=tuple(SimpleNamespace(requester_id=index, source="private query") for index in range(50)))
    await invoke(fixture, "queue")
    message = output(fixture)
    assert "@everyone" not in message and "**Улюблений**" not in message
    assert "private query" not in message
    assert "У черзі: 50" in message
    assert message.count("Замовлення користувача") == 10
    assert len(message.encode("utf-16-le")) // 2 < 2000


async def test_empty_queue_does_not_connect(invocation):
    await invoke(invocation, "queue")
    assert "не підключений" in output(invocation)
    invocation.channel.connect.assert_not_awaited()


@pytest.mark.parametrize("manager,own,automatic,allowed", [(False, False, False, False),
    (False, True, False, True), (False, False, True, True), (True, False, False, True)])
async def test_skip_ownership_manager_or_autoplay(invocation, manager, own, automatic, allowed):
    fixture = invocation
    fixture.actor.guild_permissions = discord.Permissions(manage_guild=manager)
    await fixture.cog._ensure(fixture.guild, fixture.channel)
    fixture.player.snapshot.return_value.update(request=SimpleNamespace(requester_id=20 if own else 88),
                                               autoplay=automatic)
    if allowed:
        await invoke(fixture, "skip")
        fixture.player.skip.assert_awaited_once()
        assert "пропущено" in output(fixture)
    else:
        with pytest.raises(UserError, match="власний"):
            await invoke(fixture, "skip")
        fixture.player.skip.assert_not_awaited()


async def test_stop_disables_auto_rejoin_and_closes_only_owned_session(invocation):
    fixture = invocation
    await fixture.cog._ensure(fixture.guild, fixture.channel)
    await invoke(fixture, "stop")
    fixture.manager.detach.assert_awaited_once_with(10)
    fixture.voice.disconnect.assert_awaited_once_with(force=True)
    assert fixture.cog.sessions == {} and 10 in fixture.cog._stopped_guilds
    assert "очищено" in output(fixture)
    fixture.cog.options.autojoin_channels = (30,)
    fixture.cog.bot.get_channel.return_value = fixture.channel
    await fixture.cog.autojoin.coro(fixture.cog)
    assert fixture.channel.connect.await_count == 1
    await fixture.cog._ensure(fixture.guild, fixture.channel)
    assert 10 not in fixture.cog._stopped_guilds


async def test_autoplay_setting_affects_session_only(invocation):
    await invoke(invocation, "autoplay", False)
    invocation.player.set_autoplay.assert_awaited_once_with(False)
    assert "поточної сесії" in output(invocation)


async def test_auto_pause_uses_humans_and_resumes(invocation):
    fixture = invocation
    await fixture.cog._ensure(fixture.guild, fixture.channel)
    fixture.voice.is_playing.return_value = True
    fixture.channel.members = [fixture.bot_member]
    fixture.cog._pause(10)
    fixture.voice.pause.assert_called_once()
    fixture.voice.is_playing.return_value = False
    fixture.voice.is_paused.return_value = True
    fixture.channel.members = [fixture.actor, fixture.bot_member]
    fixture.cog._pause(10)
    fixture.voice.resume.assert_called_once()


async def test_disabled_auto_pause_keeps_empty_channel_running(invocation):
    fixture = invocation
    fixture.cog.options.auto_pause = False
    fixture.channel.members = [fixture.bot_member]
    await fixture.cog._ensure(fixture.guild, fixture.channel)
    assert fixture.cog.sessions[10].backend._paused is False
    fixture.voice.pause.assert_not_called()


async def test_voice_disconnect_detaches_and_reaps_owned_backend(invocation):
    fixture = invocation
    await fixture.cog._ensure(fixture.guild, fixture.channel)
    fixture.voice.is_connected.return_value = False
    await fixture.cog.on_voice_state_update(fixture.bot_member, SimpleNamespace(channel=fixture.channel),
                                            SimpleNamespace(channel=None))
    assert fixture.cog.sessions == {}
    fixture.manager.detach.assert_awaited_once_with(10)
    fixture.voice.disconnect.assert_awaited_once()


async def test_cog_unload_closes_workers_and_voiceclients(invocation):
    fixture = invocation
    await fixture.cog._ensure(fixture.guild, fixture.channel)
    await fixture.cog.cog_unload()
    fixture.manager.close.assert_awaited_once()
    fixture.voice.disconnect.assert_awaited_once()
    assert fixture.cog.sessions == {}


async def test_configured_autojoin_bounded_and_does_not_duplicate(invocation):
    fixture = invocation
    fixture.cog.options.autojoin_channels = (30,)
    fixture.cog.bot.get_channel.return_value = fixture.channel
    await fixture.cog.autojoin.coro(fixture.cog)
    await fixture.cog.autojoin.coro(fixture.cog)
    fixture.channel.connect.assert_awaited_once()


async def test_autojoin_logs_only_error_type_and_guild(invocation, caplog):
    fixture = invocation
    fixture.cog.options.autojoin_channels = (30,)
    fixture.cog.bot.get_channel.return_value = fixture.channel
    fixture.guild.fetch_member.side_effect = RuntimeError("secret password endpoint")
    await fixture.cog.autojoin.coro(fixture.cog)
    assert "RuntimeError" in caplog.text and "guild=10" in caplog.text
    assert "secret" not in caplog.text and "password" not in caplog.text


@pytest.fixture
async def backend(monkeypatch):
    voice = MagicMock()
    voice.is_connected.return_value = True
    voice.is_playing.return_value = False
    voice.is_paused.return_value = False
    voice.disconnect = AsyncMock()
    async def disconnect(**kwargs):
        voice.is_connected.return_value = False

    voice.disconnect.side_effect = disconnect
    raw, wrapped = MagicMock(), MagicMock()
    factory = MagicMock(return_value=raw)
    transformer = MagicMock(return_value=wrapped)
    monkeypatch.setattr(module.discord, "FFmpegPCMAudio", factory)
    monkeypatch.setattr(module.discord, "PCMVolumeTransformer", transformer)
    fixture = SimpleNamespace(backend=FFmpegVoiceBackend(voice, "D:/Каталог з пробілами/ffmpeg.exe", .25),
                              voice=voice, raw=raw, wrapped=wrapped, factory=factory, transformer=transformer)
    yield fixture
    await fixture.backend.close()


def track(url="https://r1.googlevideo.com/videoplayback?signed=value"):
    return SimpleNamespace(stream_url=url)


async def test_backend_uses_fixed_arguments_protocol_allowlist_and_quoted_executable_as_value(backend):
    callback = MagicMock()
    backend.backend.play(track(), callback)
    arguments = backend.factory.call_args
    assert arguments.args == (track().stream_url,)
    assert arguments.kwargs["executable"] == "D:/Каталог з пробілами/ffmpeg.exe"
    assert "-protocol_whitelist https,tls,tcp,crypto" in arguments.kwargs["before_options"]
    assert "-nostdin" in arguments.kwargs["before_options"]
    assert "-rw_timeout 15000000" in arguments.kwargs["before_options"]
    assert arguments.kwargs["stderr"] == module.subprocess.DEVNULL
    backend.transformer.assert_called_once_with(backend.raw, volume=.25)
    backend.voice.play.assert_called_once()
    owned = backend.voice.play.call_args.args[0]
    assert owned.source is backend.wrapped
    backend.voice.play.call_args.kwargs["after"](None)
    await asyncio.sleep(0)
    callback.assert_not_called()
    # Discord's AudioPlayer calls after() before cleaning its AudioSource.
    await asyncio.to_thread(owned.cleanup)
    await eventually(lambda: callback.called)
    callback.assert_called_once_with(None)
    backend.wrapped.cleanup.assert_called_once()
    assert backend.backend._source is None


@pytest.mark.parametrize("url", ["http://r.googlevideo.com/x", "file:///tmp/secret", "https://127.0.0.1/x",
    "https://r.googlevideo.com.evil.test/x", "https://googlevideo.com@evil.test/x",
    "https://user:secret@r.googlevideo.com/x", "https://r.googlevideo.com:8443/x",
    "https://r.googlevideo.com:bad/x", "https://r.googlevideo.com/x#fragment",
    "https://evilgooglevideo.com/x", "concat:https://r.googlevideo.com/x|file:///etc/passwd"])
async def test_backend_rejects_unsafe_stream_before_any_process(backend, url):
    with pytest.raises(MusicError):
        backend.backend.play(track(url), MagicMock())
    backend.factory.assert_not_called()


async def test_backend_reaps_source_if_voice_play_raises_and_hides_raw_error(backend):
    backend.voice.play.side_effect = RuntimeError("secret stream URL")
    with pytest.raises(MusicError) as error:
        backend.backend.play(track(), MagicMock())
    assert "secret" not in str(error.value)
    assert await backend.backend.wait_stopped()
    backend.wrapped.cleanup.assert_called_once()
    assert backend.backend._source is None


async def test_backend_reaps_raw_source_if_transformer_raises(backend):
    backend.transformer.side_effect = RuntimeError("bad transformer")
    with pytest.raises(MusicError):
        backend.backend.play(track(), MagicMock())
    assert await backend.backend.wait_stopped()
    backend.raw.cleanup.assert_called_once()


async def test_stop_reaps_source_and_old_callback_cannot_clear_new_track(backend):
    callback = MagicMock()
    backend.backend.play(track(), callback)
    old_after = backend.voice.play.call_args.kwargs["after"]
    old_source = backend.voice.play.call_args.args[0]
    backend.backend.stop()
    assert await backend.backend.wait_stopped()
    backend.wrapped.cleanup.assert_called_once()
    newer = MagicMock()
    backend.transformer.return_value = newer
    backend.backend.play(track(), callback)
    old_after(None)
    await asyncio.to_thread(old_source.cleanup)
    await asyncio.sleep(0)
    assert backend.backend._source.source is newer
    backend.wrapped.cleanup.assert_called_once()
    assert callback.call_count == 1


async def test_backend_pauses_track_immediately_if_empty_channel_pause_was_set(backend):
    backend.backend.set_paused(True)
    backend.backend.play(track(), MagicMock())
    backend.voice.pause.assert_called_once()


async def test_slow_cleanup_keeps_loop_responsive_and_new_processes_bounded(backend):
    started, release = threading.Event(), threading.Event()

    def slow_cleanup():
        started.set()
        release.wait(2)

    backend.wrapped.cleanup.side_effect = slow_cleanup
    backend.backend.play(track(), MagicMock())
    first_source = backend.voice.play.call_args.args[0]
    before = time.monotonic()
    backend.backend.stop()
    try:
        await eventually(started.is_set)
        await asyncio.sleep(.01)
        assert time.monotonic() - before < .5
        cleanup_task = backend.backend._cleanup_task
        for _ in range(10):
            backend.backend.stop()
            with pytest.raises(MusicError, match="завершується"):
                backend.backend.play(track(), MagicMock())
        assert backend.backend._cleanup_task is cleanup_task
        assert not await backend.backend.wait_stopped(timeout=.01)
        assert backend.factory.call_count == 1
        # AudioPlayer can race the requested cleanup, but never clean twice.
        await asyncio.to_thread(first_source.cleanup)
        backend.wrapped.cleanup.assert_called_once()
    finally:
        release.set()
        assert await backend.backend.wait_stopped()
    backend.transformer.return_value = MagicMock()
    backend.backend.play(track(), MagicMock())
    assert backend.factory.call_count == 2


async def test_audio_thread_callback_is_delivered_on_event_loop_once(backend):
    event_loop_thread = threading.get_ident()
    threads = []
    callback = MagicMock(side_effect=lambda error: threads.append(threading.get_ident()))
    backend.backend.play(track(), callback)
    after = backend.voice.play.call_args.kwargs["after"]
    source = backend.voice.play.call_args.args[0]

    def finish():
        after(None)
        source.cleanup()
        after(RuntimeError("late callback"))
        source.cleanup()

    await asyncio.to_thread(finish)
    await eventually(lambda: callback.called)
    callback.assert_called_once_with(None)
    assert threads == [event_loop_thread]
    backend.wrapped.cleanup.assert_called_once()


async def test_disconnect_error_is_sanitized_and_cleanup_still_happens(backend, caplog):
    backend.backend.play(track(), MagicMock())
    backend.voice.disconnect.side_effect = RuntimeError("private URL secret")
    assert await backend.backend.close() is False
    backend.wrapped.cleanup.assert_called_once()
    assert "RuntimeError" in caplog.text and "private" not in caplog.text


def test_music_group_metadata_and_management_defaults(invocation):
    group = invocation.cog.__cog_app_commands_group__
    assert group.name == "music"
    assert {command.name for command in group.commands} == {"play", "join", "queue", "skip", "stop", "autoplay", "pause", "resume"}
    for command in group.commands:
        assert command.guild_only is True
        if command.name in {"join", "stop", "autoplay", "pause", "resume"}:
            assert command.default_permissions.manage_guild is True
