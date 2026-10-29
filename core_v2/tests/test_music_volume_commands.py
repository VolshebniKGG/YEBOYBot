"""Volume slash callbacks through the real worker and PCM adapter, without I/O."""
import asyncio
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core_v2 import music as module
from core_v2.help_catalog import collect_commands, render_help
from core_v2.music import FFmpegVoiceBackend, Session
from core_v2.music_engine import MusicError, MusicManager, Track
from core_v2.music_options import MusicOptions, load_music_options
from core_v2.security import UserError
from core_v2.tests.test_music_commands import eventually, invocation as invocation, invoke, output


class PCM(discord.AudioSource):
    """An endless local PCM frame, with no FFmpeg process or media request."""

    def __init__(self):
        self.cleanup = MagicMock()

    def read(self):
        return struct.pack("<h", 1000) * 1920


@pytest.fixture
async def volume(invocation, monkeypatch):
    f = invocation
    f.raw_sources = []

    def media(*args, **kwargs):
        raw = PCM()
        f.raw_sources.append(raw)
        return raw

    def play(source, **kwargs):
        f.voice.source = source
        f.voice.is_playing.return_value = True
        f.voice.is_paused.return_value = False

    def pause():
        f.voice.is_playing.return_value = False
        f.voice.is_paused.return_value = True

    def resume():
        f.voice.is_playing.return_value = True
        f.voice.is_paused.return_value = False

    def stop():
        f.voice.source = None
        f.voice.is_playing.return_value = False
        f.voice.is_paused.return_value = False

    f.voice.source = None
    f.voice.play.side_effect = play
    f.voice.pause.side_effect = pause
    f.voice.resume.side_effect = resume
    f.voice.stop.side_effect = stop
    f.media = MagicMock(side_effect=media)
    monkeypatch.setattr(module.discord, "FFmpegPCMAudio", f.media)
    f.resolver = SimpleNamespace(resolve=AsyncMock(side_effect=lambda source: Track(
        source, "https://a.googlevideo.com/audio?signed=private", source, 180)))
    f.cog.options = MusicOptions(use_autoplay=False, auto_pause=True)
    f.cog.manager = MusicManager(f.cog.options, [], f.resolver)
    f.original_ensure = f.cog._ensure
    f.session = await f.original_ensure(f.guild, f.channel)
    f.player = f.session.player
    f.setter = AsyncMock(wraps=f.player.set_volume)
    f.player.set_volume = f.setter
    f.channel.connect.reset_mock()
    f.guild.fetch_member.reset_mock()
    f.cog._ensure = AsyncMock(side_effect=AssertionError("volume must never connect"))
    try:
        yield f
    finally:
        await f.cog.cog_unload()


async def start(f, title="first"):
    await f.player.enqueue(title, 20)
    await eventually(lambda: f.voice.play.call_count > 0 and f.player.current is not None)
    return f.voice.source.source


async def finish(f):
    source = f.voice.source
    after = f.voice.play.call_args.kwargs["after"]
    f.voice.is_playing.return_value = False
    f.voice.is_paused.return_value = False
    f.voice.source = None
    after(None)
    await asyncio.to_thread(source.cleanup)


def sample(source):
    return struct.unpack_from("<h", source.read())[0]


@pytest.mark.parametrize("percent", [0, 1, 37, 100])
async def test_volume_changes_existing_session_privately_without_connecting(volume, percent):
    f = volume
    await invoke(f, "volume", percent)
    assert f.session.backend.volume == percent / 100
    f.setter.assert_awaited_once_with(percent)
    assert [call.args[0] for call in f.guild.fetch_member.await_args_list] == [20, 99]
    f.interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    assert f"{percent}%" in output(f)
    if percent == 0:
        assert "трек продовжується" not in output(f).lower()
    f.channel.connect.assert_not_awaited()
    f.cog._ensure.assert_not_awaited()
    assert f.cog.manager.players[10] is f.player
    assert f.cog.options.default_volume == .25


async def test_query_uses_configured_idle_volume_and_has_no_side_effect(volume):
    f = volume
    await invoke(f, "volume")
    assert "25%" in output(f) and "наступних" in output(f).lower()
    f.setter.assert_not_awaited()
    f.media.assert_not_called()
    f.resolver.resolve.assert_not_awaited()
    f.cog._ensure.assert_not_awaited()


async def test_live_pcm_changes_immediately_and_muting_keeps_fifo_advancing(volume):
    f = volume
    source = await start(f)
    assert isinstance(source, discord.PCMVolumeTransformer)
    assert sample(source) == 250
    current, request, worker = f.player.current, f.player.request, f.player._task
    await f.player.enqueue("second", 20)
    await f.player.enqueue("third", 20)
    pending = tuple(f.player.queue)
    f.player._failures = 3
    await invoke(f, "volume", 0)
    assert sample(source) == 0
    assert f.player.current is current and f.player.request is request
    assert f.player._task is worker and not worker.done()
    assert tuple(f.player.queue) == pending and f.player._failures == 3
    assert f.player.manual_paused is False
    assert f.voice.is_playing() is True
    f.voice.pause.assert_not_called()
    f.voice.resume.assert_not_called()
    f.voice.stop.assert_not_called()
    await finish(f)
    await eventually(lambda: f.player.current is not None and f.player.current.title == "second")
    assert sample(f.voice.source.source) == 0
    assert [request.source for request in f.player.queue] == ["third"]
    await invoke(f, "volume", 100)
    assert sample(f.voice.source.source) == 1000
    assert f.resolver.resolve.await_args_list[0].args == ("first",)
    assert f.resolver.resolve.await_args_list[1].args == ("second",)


async def test_query_reports_observed_active_pcm_instead_of_cached_setting(volume):
    f = volume
    source = await start(f)
    source.volume = .64
    await invoke(f, "volume", None)
    current, future = output(f).split(".", 1)
    assert "64%" in current and "25%" not in current
    assert "наступних" in future.lower() and "25%" in future
    assert f.session.backend.volume == .25
    assert source.volume == .64
    f.setter.assert_not_awaited()


@pytest.mark.parametrize("observation", ["unknown-playing", "unknown-paused", "readback", "readback-raises"])
async def test_query_preserves_unknown_state_without_claiming_audibility(volume, monkeypatch, observation, caplog):
    f = volume
    source = await start(f)
    if observation == "unknown-playing":
        f.voice.is_playing.return_value = None
        f.voice.is_paused.return_value = False
    elif observation == "unknown-paused":
        f.voice.is_playing.return_value = False
        f.voice.is_paused.return_value = None
    elif observation == "readback":
        source._volume = float("nan")
    else:
        def broken(_source):
            raise RuntimeError("https://private.example/?password=secret")
        monkeypatch.setattr(discord.PCMVolumeTransformer, "volume", property(broken))
    await invoke(f, "volume")
    message = output(f).lower()
    assert "невідом" in message or "непідтвердж" in message
    assert "25%" not in message.split(".", 1)[0]
    assert "наступних" in message and "чути" not in message and "чутно" not in message
    assert "private.example" not in message + caplog.text and "password=secret" not in message + caplog.text
    f.setter.assert_not_awaited()


async def test_volume_preserves_manual_and_empty_channel_pause_intent(volume):
    f = volume
    source = await start(f)
    await invoke(f, "pause")
    f.channel.members = [f.bot_member]
    f.cog._pause(10)
    before = f.session.backend.pause_state()
    queued = await f.player.enqueue("held", 20)
    f.voice.pause.reset_mock()
    f.voice.resume.reset_mock()
    await invoke(f, "volume", 0)
    assert sample(source) == 0
    assert f.player.manual_paused is True and queued == 1
    assert f.session.backend.pause_state() == before
    assert f.player.autoplay_enabled is False
    assert "трек продовжується" not in output(f).lower()
    f.voice.pause.assert_not_called()
    f.voice.resume.assert_not_called()


async def test_volume_and_retry_preserve_existing_worker_backoff(volume):
    f = volume
    f.resolver.resolve.side_effect = MusicError("private unavailable media")
    await f.player.enqueue("failed", 20)
    await eventually(lambda: f.player.state == "backoff")
    worker, error = f.player._task, f.player.error
    assert f.player._failures == 1 and not f.player._changed.is_set()
    await invoke(f, "volume", 0)
    await invoke(f, "volume", 0)
    assert f.player.state == "backoff" and f.player._task is worker
    assert f.player._failures == 1 and f.player.error == error
    assert not f.player._changed.is_set()
    f.resolver.resolve.assert_awaited_once_with("failed")
    f.media.assert_not_called()


async def test_volume_is_per_guild_and_reconnect_reloads_ini_default(volume, tmp_path):
    f = volume
    config = tmp_path / "config"
    config.mkdir()
    path = config / "options.ini"
    contents = "[MusicBot]\nDefaultVolume = 0.41\nUseAutoPlaylist = no\n[Chat]\n"
    path.write_text(contents, encoding="utf-8")
    f.cog.options = load_music_options(tmp_path)
    other_voice = MagicMock()
    other_voice.is_connected.return_value = True
    other_voice.is_playing.return_value = False
    other_voice.is_paused.return_value = False
    other_backend = FFmpegVoiceBackend(other_voice, "never-launched-ffmpeg", .41)
    other_player = f.cog.manager.attach(11, other_backend)
    f.cog.sessions[11] = Session(other_backend, other_player)
    try:
        await invoke(f, "volume", 77)
        assert other_backend.volume == .41
        await f.cog._remove(10)
        replacement = await f.original_ensure(f.guild, f.channel)
        assert replacement is not f.session
        assert replacement.backend.volume == .41
        assert f.session.backend.volume == .77
        assert path.read_text(encoding="utf-8") == contents
    finally:
        f.cog.sessions.pop(11, None)
        await f.cog.manager.detach(11)


@pytest.mark.parametrize("percent", [True, False, 1.0, .5, float("nan"), float("inf"),
                                      -1, 101, "50", [], {}], ids=str)
async def test_direct_volume_callback_rejects_invalid_values_without_mutation(volume, percent):
    f = volume
    backend_setter = MagicMock(wraps=f.session.backend.set_volume)
    f.session.backend.set_volume = backend_setter
    with pytest.raises(UserError):
        await invoke(f, "volume", percent)
    assert f.session.backend.volume == .25
    backend_setter.assert_not_called()
    f.cog._ensure.assert_not_awaited()
    f.interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize("percent", [None, 42], ids=["query", "set"])
@pytest.mark.parametrize("problem", ["dm", "non-member", "no-manager", "fresh-no-manager",
    "no-voice", "stage", "afk", "other-channel", "foreign-channel", "actor-guild",
    "bot-guild", "actor-bot", "no-session", "other-guild-session", "closing", "close-failed",
    "no-player", "closed-worker", "finished-worker", "disconnected", "unknown-connection",
    "unload", "removed-guild", "noncanonical-guild", "unavailable"], ids=str)
async def test_volume_rejects_unauthorized_or_obsolete_context(volume, percent, problem):
    f = volume
    real_task = f.player._task
    moved = False
    if problem == "dm":
        f.interaction.guild = None
    elif problem == "non-member":
        f.interaction.user = MagicMock(spec=discord.User)
    elif problem == "no-manager":
        f.actor.guild_permissions = discord.Permissions.none()
    elif problem == "fresh-no-manager":
        fresh = MagicMock(spec=discord.Member)
        fresh.id, fresh.guild, fresh.bot = 20, f.guild, False
        fresh.voice = f.actor.voice
        fresh.guild_permissions = discord.Permissions.none()
        f.guild.fetch_member.side_effect = lambda user_id: fresh if user_id == 20 else f.bot_member
        assert f.interaction.user.guild_permissions.manage_guild
    elif problem == "no-voice":
        f.actor.voice = None
    elif problem in {"stage", "other-channel", "foreign-channel"}:
        channel = MagicMock(spec=discord.StageChannel if problem == "stage" else discord.VoiceChannel)
        channel.id = 31 if problem == "other-channel" else 30
        channel.guild = SimpleNamespace(id=11) if problem == "foreign-channel" else f.guild
        channel.permissions_for.return_value = discord.Permissions.all()
        f.actor.voice = SimpleNamespace(channel=channel)
    elif problem == "afk":
        f.guild.afk_channel = f.channel
    elif problem in {"actor-guild", "bot-guild"}:
        (f.actor if problem == "actor-guild" else f.bot_member).guild = SimpleNamespace(id=11)
    elif problem == "actor-bot":
        f.actor.bot = True
    elif problem == "no-session":
        f.cog.sessions.pop(10)
    elif problem == "other-guild-session":
        f.cog.sessions[11] = f.cog.sessions.pop(10)
        moved = True
    elif problem == "closing":
        f.session.closing = True
    elif problem == "close-failed":
        f.session.close_failed = True
    elif problem == "no-player":
        f.session.player = None
    elif problem == "closed-worker":
        f.player._closed = True
    elif problem == "finished-worker":
        f.player._task = asyncio.get_running_loop().create_future()
        f.player._task.set_result(None)
    elif problem in {"disconnected", "unknown-connection"}:
        f.voice.is_connected.return_value = False if problem == "disconnected" else None
    elif problem == "unload":
        f.cog._closing = True
    elif problem == "removed-guild":
        f.cog.bot.get_guild.return_value = None
    elif problem == "noncanonical-guild":
        f.cog.bot.get_guild.return_value = SimpleNamespace(id=10)
    elif problem == "unavailable":
        f.guild.unavailable = True
    try:
        with pytest.raises(UserError):
            await invoke(f, "volume", percent)
        assert f.session.backend.volume == .25
        f.setter.assert_not_awaited()
        f.channel.connect.assert_not_awaited()
        f.cog._ensure.assert_not_awaited()
        f.interaction.followup.send.assert_not_awaited()
        if problem in {"dm", "non-member"}:
            f.interaction.response.defer.assert_not_awaited()
            f.guild.fetch_member.assert_not_awaited()
    finally:
        f.player._task = real_task
        f.player._closed = False
        f.session.player = f.player
        if moved:
            f.cog.sessions.pop(11)
        f.cog.sessions[10] = f.session


@pytest.mark.parametrize("member", ["actor", "bot"])
@pytest.mark.parametrize("permission", ["view_channel", "connect", "speak"])
async def test_volume_rechecks_both_members_channel_permissions(volume, member, permission):
    f = volume
    target = f.actor if member == "actor" else f.bot_member

    def permissions(subject):
        result = discord.Permissions(view_channel=True, connect=True, speak=True)
        if subject is target:
            setattr(result, permission, False)
        return result

    f.channel.permissions_for.side_effect = permissions
    with pytest.raises(UserError):
        await invoke(f, "volume", 50)
    f.setter.assert_not_awaited()


@pytest.mark.parametrize("change", ["stop", "remove-guild", "unload", "voice-moved"])
async def test_volume_during_member_rest_never_mutates_old_or_unavailable_session(volume, change):
    f = volume
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(user_id):
        entered.set()
        await release.wait()
        return f.actor if user_id == 20 else f.bot_member

    f.guild.fetch_member.side_effect = fetch
    task = asyncio.create_task(invoke(f, "volume", 81))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if change == "stop":
            await f.cog._remove(10)
        elif change == "remove-guild":
            f.cog.bot.get_guild.return_value = None
        elif change == "unload":
            f.cog._closing = True
        else:
            f.voice.channel = SimpleNamespace(id=31, guild=f.guild)
        release.set()
        with pytest.raises(UserError):
            await task
        f.setter.assert_not_awaited()
        assert f.session.backend.volume == .25
        f.cog._ensure.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_member_rest_cancellation_leaves_volume_unchanged(volume):
    f = volume
    entered = asyncio.Event()

    async def fetch(_user_id):
        entered.set()
        await asyncio.Future()

    f.guild.fetch_member.side_effect = fetch
    task = asyncio.create_task(invoke(f, "volume", 42))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    f.setter.assert_not_awaited()
    assert f.session.backend.volume == .25


@pytest.mark.parametrize("error", [RuntimeError("https://private.example/?password=secret"),
    MusicError("https://private.example/?password=secret"), TimeoutError("password=secret")], ids=str)
async def test_volume_setter_errors_are_sanitized(volume, error, caplog):
    f = volume
    f.setter.side_effect = error
    with pytest.raises(UserError) as result:
        await invoke(f, "volume", 42)
    assert "private.example" not in str(result.value) + caplog.text
    assert "password=secret" not in str(result.value) + caplog.text
    assert f.session.backend.volume == .25
    f.interaction.followup.send.assert_not_awaited()


async def test_partial_sdk_write_is_an_error_and_query_exposes_actual_and_future_values(volume, monkeypatch, caplog):
    f = volume
    source = await start(f)
    descriptor = discord.PCMVolumeTransformer.volume

    def broken_setter(transformer, value):
        descriptor.fset(transformer, value)
        raise RuntimeError("https://private.example/?password=secret")

    monkeypatch.setattr(discord.PCMVolumeTransformer, "volume", property(descriptor.fget, broken_setter))
    with pytest.raises(UserError) as result:
        await invoke(f, "volume", 60)
    assert source.volume == .60 and f.session.backend.volume == .25
    assert "private.example" not in str(result.value) + caplog.text
    assert "password=secret" not in str(result.value) + caplog.text
    f.interaction.followup.send.assert_not_awaited()
    monkeypatch.setattr(discord.PCMVolumeTransformer, "volume", descriptor)
    await invoke(f, "volume")
    current, future = output(f).split(".", 1)
    assert "60%" in current and "25%" not in current
    assert "наступних" in future.lower() and "25%" in future
    assert f.voice.play.call_count == 1
    f.voice.stop.assert_not_called()


async def test_volume_member_rest_failure_is_sanitized(volume, caplog):
    f = volume
    response = SimpleNamespace(status=503, reason="Service unavailable")
    f.guild.fetch_member.side_effect = discord.HTTPException(response, "password=secret")
    with pytest.raises(UserError) as result:
        await invoke(f, "volume", 42)
    assert "password=secret" not in str(result.value) + caplog.text
    f.setter.assert_not_awaited()


async def test_volume_worker_cancellation_propagates_without_success(volume):
    f = volume
    f.setter.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await invoke(f, "volume", 42)
    f.interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize("delivery_error", [RuntimeError("synthetic-delivery"), asyncio.CancelledError()], ids=str)
async def test_lost_or_cancelled_followup_keeps_volume_and_retry_has_no_playback_effect(volume, delivery_error):
    f = volume
    source = await start(f)
    await f.player.enqueue("second", 20)
    pending, current, worker = tuple(f.player.queue), f.player.current, f.player._task
    f.player._failures = 4
    f.interaction.followup.send.side_effect = delivery_error
    with pytest.raises(type(delivery_error)):
        await invoke(f, "volume", 55)
    assert source.volume == .55 and f.session.backend.volume == .55
    f.interaction.followup.send.side_effect = None
    await invoke(f, "volume", 55)
    assert source.volume == .55 and output(f)
    assert f.player.current is current and f.player._task is worker
    assert tuple(f.player.queue) == pending and f.player._failures == 4
    assert f.voice.play.call_count == 1
    f.voice.pause.assert_not_called()
    f.voice.resume.assert_not_called()
    f.voice.stop.assert_not_called()
    f.cog._ensure.assert_not_awaited()


async def test_real_music_tree_has_nine_commands_with_optional_integer_volume_and_bounded_help(volume):
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    group = volume.cog.__cog_app_commands_group__
    tree.add_command(group)
    tree.sync = AsyncMock(side_effect=AssertionError("must not publish"))
    tree.fetch_commands = AsyncMock(side_effect=AssertionError("must not fetch"))
    expected = {"play", "join", "queue", "skip", "stop", "autoplay", "pause", "resume", "volume"}
    assert {command.name for command in group.commands} == expected
    command = group.get_command("volume")
    assert command.guild_only is True and command.default_permissions.manage_guild is True
    parameter, = command.parameters
    assert parameter.name == "percent" and parameter.required is False
    assert parameter.default is None and parameter.type is discord.AppCommandOptionType.integer
    assert parameter.min_value == 0 and parameter.max_value == 100
    entries = collect_commands(tree)
    commands = {item.name: item for item in entries if item.category == "music"}
    assert set(commands) == {"music " + name for name in expected}
    assert "керувати сервером" in commands["music volume"].permissions.lower()
    embed = render_help(entries, "music")
    parts = [embed.title or "", embed.description or "", embed.footer.text or ""]
    for field in embed.fields:
        assert len(field.name.encode("utf-16-le")) // 2 <= 256
        assert len(field.value.encode("utf-16-le")) // 2 <= 1024
        parts.extend([field.name, field.value])
    assert sum(len(part.encode("utf-16-le")) // 2 for part in parts) <= 6000
    assert "volume" in str(embed.to_dict())
    tree.sync.assert_not_awaited()
    tree.fetch_commands.assert_not_awaited()
