"""Music control callbacks with real session/worker policy and no network."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core_v2.help_catalog import collect_commands, render_help
from core_v2.music import Music
from core_v2.music_engine import MusicError, MusicManager
from core_v2.music_options import MusicOptions
from core_v2.security import UserError
from core_v2.tests.test_music_commands import invocation as invocation, invoke, output


@pytest.fixture
async def controls(invocation):
    f = invocation
    options = MusicOptions(use_autoplay=False, auto_pause=True)
    resolver = SimpleNamespace(resolve=AsyncMock(side_effect=AssertionError("must not extract while idle")))
    manager = MusicManager(options, [], resolver)
    f.cog.manager, f.cog.options = manager, options
    f.session = await f.cog._ensure(f.guild, f.channel)
    f.original_setter = f.session.player.set_manual_paused
    f.setter = AsyncMock(wraps=f.original_setter)
    f.session.player.set_manual_paused = f.setter
    f.channel.connect.reset_mock()
    f.guild.fetch_member.reset_mock()
    f.cog._ensure = AsyncMock(side_effect=AssertionError("controls must not connect"))
    try:
        yield f
    finally:
        await f.cog.cog_unload()


async def prime(f, command):
    if command == "resume":
        await f.original_setter(True)
    f.setter.reset_mock()


@pytest.mark.parametrize("command,enabled", [("pause", True), ("resume", False)])
async def test_manual_control_only_changes_existing_session_and_replies_privately(controls, command, enabled):
    f = controls
    await prime(f, command)
    await invoke(f, command)
    assert f.session.player.snapshot()["manual_paused"] is enabled
    f.setter.assert_awaited_once_with(enabled)
    assert [call.args[0] for call in f.guild.fetch_member.await_args_list] == [20, 99]
    f.interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    assert output(f)
    f.channel.connect.assert_not_awaited()
    f.cog._ensure.assert_not_awaited()
    assert f.cog.manager.players[10] is f.session.player


@pytest.mark.parametrize("command,enabled", [("pause", True), ("resume", False)])
async def test_duplicate_manual_control_is_idempotent_in_real_worker(controls, command, enabled):
    f = controls
    await prime(f, command)
    await invoke(f, command)
    await invoke(f, command)
    assert f.session.player.snapshot()["manual_paused"] is enabled
    assert f.setter.await_args_list[0].args == (enabled,)
    assert f.setter.await_args_list[1].args == (enabled,)
    f.channel.connect.assert_not_awaited()


@pytest.mark.parametrize("command", ["pause", "resume"])
async def test_control_without_session_never_creates_one(controls, command):
    f = controls
    await f.cog._remove(10)
    f.setter.reset_mock()
    with pytest.raises(UserError):
        await invoke(f, command)
    f.setter.assert_not_awaited()
    f.channel.connect.assert_not_awaited()
    f.cog._ensure.assert_not_awaited()
    assert not f.cog.sessions


@pytest.mark.parametrize("command", ["pause", "resume"])
async def test_control_cannot_use_another_guild_session(controls, command):
    f = controls
    f.cog.sessions[11] = f.cog.sessions.pop(10)
    try:
        with pytest.raises(UserError):
            await invoke(f, command)
        f.setter.assert_not_awaited()
        f.channel.connect.assert_not_awaited()
    finally:
        f.cog.sessions[10] = f.cog.sessions.pop(11)


@pytest.mark.parametrize("command", ["pause", "resume"])
@pytest.mark.parametrize("context", ["dm", "user-not-member"])
async def test_control_requires_actual_guild_member_context(controls, command, context):
    f = controls
    if context == "dm":
        f.interaction.guild = None
    else:
        f.interaction.user = MagicMock(spec=discord.User)
    with pytest.raises(UserError):
        await invoke(f, command)
    f.guild.fetch_member.assert_not_awaited()
    f.interaction.response.defer.assert_not_awaited()
    f.setter.assert_not_awaited()


@pytest.mark.parametrize("command", ["pause", "resume"])
@pytest.mark.parametrize("problem", [
    "no-manager", "actor-is-bot", "actor-id", "actor-guild", "bot-id", "bot-guild",
    "not-in-voice", "stage-channel", "foreign-channel", "afk-channel", "other-channel",
    "closing-session", "failed-session", "no-worker", "closing-cog", "removed-guild",
], ids=str)
async def test_controls_reject_unauthorized_or_obsolete_context_without_mutation(controls, command, problem):
    f = controls
    await prime(f, command)
    other = SimpleNamespace(id=11)
    if problem == "no-manager":
        f.actor.guild_permissions = discord.Permissions.none()
    elif problem == "actor-is-bot":
        f.actor.bot = True
    elif problem == "actor-id":
        actor = MagicMock(spec=discord.Member)
        actor.id, actor.guild, actor.bot = 21, f.guild, False
        f.guild.fetch_member.side_effect = lambda user_id: actor if user_id == 20 else f.bot_member
    elif problem == "actor-guild":
        f.actor.guild = other
    elif problem == "bot-id":
        f.bot_member.id = 98
    elif problem == "bot-guild":
        f.bot_member.guild = other
    elif problem == "not-in-voice":
        f.actor.voice = None
    elif problem == "stage-channel":
        f.actor.voice.channel = MagicMock(spec=discord.StageChannel)
    elif problem == "foreign-channel":
        f.channel.guild = other
    elif problem == "afk-channel":
        f.guild.afk_channel = f.channel
    elif problem == "other-channel":
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.id, channel.guild = 31, f.guild
        channel.permissions_for.return_value = discord.Permissions.all()
        f.actor.voice.channel = channel
    elif problem == "closing-session":
        f.session.closing = True
    elif problem == "failed-session":
        f.session.close_failed = True
    elif problem == "no-worker":
        f.session.player = None
    elif problem == "closing-cog":
        f.cog._closing = True
    elif problem == "removed-guild":
        f.cog.bot.get_guild.return_value = None
    try:
        with pytest.raises(UserError):
            await invoke(f, command)
        f.setter.assert_not_awaited()
        f.channel.connect.assert_not_awaited()
        f.cog._ensure.assert_not_awaited()
        f.interaction.followup.send.assert_not_awaited()
    finally:
        # Preserve the real worker reference for fixture cleanup, even when a
        # provisional session was deliberately substituted above.
        f.session.player = f.cog.manager.players.get(10, f.session.player)


@pytest.mark.parametrize("command", ["pause", "resume"])
@pytest.mark.parametrize("member", ["actor", "bot"])
@pytest.mark.parametrize("permission", ["view_channel", "connect", "speak"])
async def test_control_rechecks_both_channel_permission_sets(controls, command, member, permission):
    f = controls
    await prime(f, command)
    target = f.actor if member == "actor" else f.bot_member

    def permissions(subject):
        result = discord.Permissions(view_channel=True, connect=True, speak=True)
        if subject is target:
            setattr(result, permission, False)
        return result

    f.channel.permissions_for.side_effect = permissions
    with pytest.raises(UserError):
        await invoke(f, command)
    f.setter.assert_not_awaited()
    f.channel.connect.assert_not_awaited()


@pytest.mark.parametrize("command", ["pause", "resume"])
async def test_control_uses_fresh_member_permission_instead_of_interaction_cache(controls, command):
    f = controls
    await prime(f, command)
    fresh = MagicMock(spec=discord.Member)
    fresh.id, fresh.guild, fresh.bot = 20, f.guild, False
    fresh.guild_permissions = discord.Permissions.none()
    fresh.voice = f.actor.voice
    f.guild.fetch_member.side_effect = lambda user_id: fresh if user_id == 20 else f.bot_member
    assert f.interaction.user.guild_permissions.manage_guild is True
    with pytest.raises(UserError, match="Керувати сервером"):
        await invoke(f, command)
    f.setter.assert_not_awaited()


@pytest.mark.parametrize("command", ["pause", "resume"])
async def test_control_rejects_disconnected_session_without_mutating_pause_intent(controls, command):
    f = controls
    await prime(f, command)
    before = f.session.player.snapshot()["manual_paused"]
    f.voice.is_connected.return_value = False
    with pytest.raises(UserError):
        await invoke(f, command)
    assert f.session.player.snapshot()["manual_paused"] is before
    f.setter.assert_not_awaited()
    f.channel.connect.assert_not_awaited()


@pytest.mark.parametrize("command", ["pause", "resume"])
async def test_member_lookup_failure_does_not_expose_discord_text(controls, command, caplog):
    f = controls
    response = SimpleNamespace(status=503, reason="Service unavailable")
    f.guild.fetch_member.side_effect = discord.HTTPException(response, "synthetic-private-credential")
    with pytest.raises(UserError) as error:
        await invoke(f, command)
    assert "synthetic-private" not in str(error.value) + caplog.text
    f.setter.assert_not_awaited()


@pytest.mark.parametrize("command", ["pause", "resume"])
@pytest.mark.parametrize("error", [RuntimeError("synthetic-private-endpoint"),
    MusicError("synthetic-private-endpoint"), TimeoutError("synthetic-private-endpoint")],
                         ids=["sdk", "music", "timeout"])
async def test_control_failure_never_returns_untrusted_exception_text(controls, command, error, caplog):
    f = controls
    await prime(f, command)
    f.setter.side_effect = error
    with pytest.raises(UserError) as result:
        await invoke(f, command)
    assert "synthetic-private" not in str(result.value) + caplog.text
    f.interaction.followup.send.assert_not_awaited()
    f.channel.connect.assert_not_awaited()


@pytest.mark.parametrize("command", ["pause", "resume"])
async def test_context_cancellation_propagates_without_pause_mutation(controls, command):
    f = controls
    await prime(f, command)
    entered = asyncio.Event()

    async def fetch(_user_id):
        entered.set()
        await asyncio.Future()

    f.guild.fetch_member.side_effect = fetch
    task = asyncio.create_task(invoke(f, command))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    f.setter.assert_not_awaited()
    f.interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize("command", ["pause", "resume"])
async def test_cancellation_from_worker_is_not_rewritten_as_success_or_user_error(controls, command):
    f = controls
    await prime(f, command)
    f.setter.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await invoke(f, command)
    f.interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize("command", ["pause", "resume"])
@pytest.mark.parametrize("change", ["stop", "remove-guild", "unload"])
async def test_context_await_does_not_mutate_worker_after_lifecycle_change(controls, command, change):
    f = controls
    await prime(f, command)
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(user_id):
        entered.set()
        await release.wait()
        return f.actor if user_id == 20 else f.bot_member

    f.guild.fetch_member.side_effect = fetch
    task = asyncio.create_task(invoke(f, command))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if change == "stop":
            await f.cog._remove(10)
        elif change == "remove-guild":
            f.cog.bot.get_guild.return_value = None
        else:
            f.cog._closing = True
        release.set()
        with pytest.raises(UserError):
            await task
        f.setter.assert_not_awaited()
        f.channel.connect.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_manual_resume_keeps_empty_channel_autopause(controls):
    f = controls
    await invoke(f, "pause")
    f.channel.members = [f.bot_member]
    f.cog._pause(10)
    await invoke(f, "resume")
    state = f.session.backend.pause_state()
    assert state["manual"] is False and state["automatic"] is True
    assert f.session.player.snapshot()["manual_paused"] is False
    assert "автомат" in output(f).lower() or "порож" in output(f).lower()


async def test_human_arrival_cannot_clear_manual_pause(controls):
    f = controls
    await invoke(f, "pause")
    f.channel.members = [f.bot_member]
    f.cog._pause(10)
    f.channel.members = [f.actor, f.bot_member]
    f.cog._pause(10)
    state = f.session.backend.pause_state()
    assert state["manual"] is True and state["automatic"] is False
    assert f.session.player.snapshot()["manual_paused"] is True
    f.voice.resume.assert_not_called()


async def test_queue_reports_manual_pause_instead_of_claiming_playback(controls):
    f = controls
    f.voice.is_playing.return_value = True

    def paused():
        f.voice.is_playing.return_value = False
        f.voice.is_paused.return_value = True

    f.voice.pause.side_effect = paused
    await invoke(f, "pause")
    await invoke(f, "queue")
    message = output(f)
    assert "ручн" in message.lower() and "пауз" in message.lower()
    assert "Стан: Відтворення" not in message
    assert len(message.encode("utf-16-le")) // 2 < 2000


async def test_queue_keeps_unknown_sdk_pause_state_unknown(controls):
    f = controls
    await invoke(f, "pause")
    f.voice.is_paused.return_value = None
    await invoke(f, "queue")
    message = output(f).lower()
    assert "невідом" in message or "непідтвердж" in message
    assert "стан: відтворення" not in message


@pytest.mark.parametrize("observation", ["stopped", "unknown", "raises"])
async def test_queue_never_infers_playback_from_scheduler_state_alone(controls, observation, caplog):
    f = controls
    # The scheduler can still say playing after a native player stops or its
    # status probe fails. A lack of pause alone does not establish playback.
    await asyncio.sleep(0)
    f.session.player.state = "playing"
    f.voice.is_paused.return_value = False
    if observation == "raises":
        f.voice.is_playing.side_effect = RuntimeError("synthetic-private-player")
    else:
        f.voice.is_playing.return_value = False if observation == "stopped" else None
    await invoke(f, "queue")
    message = output(f)
    assert "Стан: Відтворення не підтверджено." in message
    assert "Стан: Відтворення." not in message
    assert "synthetic-private" not in message + caplog.text


async def test_queue_confirms_local_playback_only_when_sdk_reports_playing_and_not_paused(controls):
    f = controls
    await asyncio.sleep(0)
    f.session.player.state = "playing"
    f.voice.is_playing.return_value = True
    f.voice.is_paused.return_value = False
    await invoke(f, "queue")
    message = output(f)
    assert "Стан: Відтворення." in message
    assert "Відтворення не підтверджено" not in message


async def test_lost_followup_does_not_undo_pause_and_repeat_is_idempotent(controls):
    f = controls
    f.interaction.followup.send.side_effect = RuntimeError("synthetic-delivery-failure")
    with pytest.raises(RuntimeError, match="synthetic-delivery"):
        await invoke(f, "pause")
    assert f.session.player.snapshot()["manual_paused"] is True
    f.interaction.followup.send.side_effect = None
    await invoke(f, "pause")
    assert f.session.player.snapshot()["manual_paused"] is True
    assert output(f)
    f.channel.connect.assert_not_awaited()


def test_pause_resume_group_metadata_are_guild_management_commands():
    for command in (Music.pause, Music.resume):
        assert command.guild_only is True
        assert command.default_permissions.manage_guild is True


async def test_real_music_tree_exposes_eight_leaf_commands_and_safe_private_help(controls):
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    tree.add_command(controls.cog.__cog_app_commands_group__)
    tree.sync = AsyncMock(side_effect=AssertionError("must not publish"))
    tree.fetch_commands = AsyncMock(side_effect=AssertionError("must not fetch"))
    entries = collect_commands(tree)
    music = {item.name: item for item in entries if item.category == "music"}
    assert set(music) == {"music " + name for name in
                          ("join", "play", "queue", "skip", "stop", "autoplay", "pause", "resume")}
    for name in ("pause", "resume"):
        assert "керувати сервером" in music["music " + name].permissions.lower()
    embed = render_help(entries, "music")
    total = len((embed.title or "").encode("utf-16-le")) // 2
    total += len((embed.description or "").encode("utf-16-le")) // 2
    total += len((embed.footer.text or "").encode("utf-16-le")) // 2
    for item in embed.fields:
        assert len(item.name.encode("utf-16-le")) // 2 <= 256
        assert len(item.value.encode("utf-16-le")) // 2 <= 1024
        total += len((item.name + item.value).encode("utf-16-le")) // 2
    assert total < 6000
    assert "pause" in str(embed.to_dict()) and "resume" in str(embed.to_dict())
    tree.sync.assert_not_awaited()
    tree.fetch_commands.assert_not_awaited()

