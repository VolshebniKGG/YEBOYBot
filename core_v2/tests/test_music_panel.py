"""Ephemeral music view tests: no Discord login or live audio."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core_v2 import music as music_module
from core_v2.music_panel import MAX_PANELS, MusicPanel
from core_v2.security import UserError
from test_music_commands import invocation as music_invocation, invoke


def click(f, *, user=None, guild=None, channel_id=777):
    return SimpleNamespace(
        guild=guild or f.guild,
        guild_id=(guild or f.guild).id,
        channel_id=channel_id,
        user=user or f.actor,
        response=SimpleNamespace(
            defer=AsyncMock(), send_message=AsyncMock(), is_done=lambda: True),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock())


async def opened(f):
    f.interaction.channel_id = 777
    f.interaction.followup.send.return_value = SimpleNamespace(edit=AsyncMock())
    await invoke(f, "panel")
    view = f.interaction.followup.send.await_args.kwargs["view"]
    assert isinstance(view, MusicPanel)
    assert view in f.cog.panels
    return view


async def test_slash_panel_is_private_session_bound_and_bounded(music_invocation):
    f = music_invocation
    await f.cog._ensure(f.guild, f.channel)
    view = await opened(f)
    kwargs = f.interaction.followup.send.await_args.kwargs
    assert kwargs["ephemeral"] is True and kwargs["wait"] is True
    assert kwargs["allowed_mentions"].to_dict()["parse"] == []
    assert isinstance(kwargs["embed"], discord.Embed)
    assert len(view.children) == 7 and view.timeout == 120
    assert all(button.row in (0, 1) for button in view.children)
    assert view.session is f.cog.sessions[f.guild.id]
    assert MAX_PANELS == 64
    await view.expire()
    assert view.expired and view not in f.cog.panels
    assert all(item.disabled for item in view.children)
    view.message.edit.assert_awaited_once()


async def test_foreign_actor_and_text_channel_cannot_control_panel(music_invocation):
    f = music_invocation
    await f.cog._ensure(f.guild, f.channel)
    view = await opened(f)
    forbidden = click(f, user=SimpleNamespace(id=999))
    await view._action(forbidden, "skip")
    forbidden.response.defer.assert_not_awaited()
    assert "автору" in forbidden.followup.send.await_args.args[0]
    wrong_channel = click(f, channel_id=778)
    await view._action(wrong_channel, "pause")
    wrong_channel.response.defer.assert_not_awaited()
    f.player.skip.assert_not_awaited()
    await view.expire()


async def test_manager_buttons_check_fresh_voice_rights_on_every_click(music_invocation):
    f = music_invocation
    await f.cog._ensure(f.guild, f.channel)
    view = await opened(f)
    f.player.set_manual_paused = AsyncMock(return_value=True)
    f.actor.guild_permissions = discord.Permissions.none()
    blocked = click(f)
    await view._action(blocked, "pause")
    assert "Керувати сервером" in blocked.followup.send.await_args.args[0]
    f.player.set_manual_paused.assert_not_awaited()
    f.actor.guild_permissions = discord.Permissions(manage_guild=True)
    allowed = click(f)
    await view._action(allowed, "pause")
    f.player.set_manual_paused.assert_awaited_once_with(True)
    assert "ввімкнено" in allowed.followup.send.await_args.args[0]
    allowed.edit_original_response.assert_awaited_once()
    await view.expire()


async def test_panel_repeat_button_cycles_modes_without_faking_slash(music_invocation):
    f = music_invocation
    await f.cog._ensure(f.guild, f.channel)
    view = await opened(f)
    state = {"repeat_mode": "off", "autoplay": False, "request": None,
             "queue": (), "current": None, "state": "idle", "autoplay_enabled": False}
    f.player.snapshot = MagicMock(side_effect=lambda: state)
    async def set_repeat(value):
        state["repeat_mode"] = value
        return True
    f.player.set_repeat = AsyncMock(side_effect=set_repeat)
    for mode in ("one", "all", "off"):
        caller = click(f)
        await view._action(caller, "repeat")
        assert state["repeat_mode"] == mode
        assert caller.edit_original_response.await_count == 1
        assert caller.followup.send.await_count == 1
    assert [c.args[0] for c in f.player.set_repeat.await_args_list] == ["one", "all", "off"]
    await view.expire()


async def test_old_session_panel_denied_after_reconnect(music_invocation):
    f = music_invocation
    await f.cog._ensure(f.guild, f.channel)
    view = await opened(f)
    f.cog.sessions[10] = SimpleNamespace(player=f.player)
    caller = click(f)
    await view._action(caller, "skip")
    assert "застаріла" in caller.followup.send.await_args.args[0]
    f.player.skip.assert_not_awaited()
    await view.expire()


async def test_panel_skip_respects_current_track_ownership(music_invocation):
    f = music_invocation
    await f.cog._ensure(f.guild, f.channel)
    view = await opened(f)
    f.player.snapshot.return_value = {"request": SimpleNamespace(requester_id=999),
                                      "autoplay": False, "queue": (), "current": None,
                                      "state": "playing", "autoplay_enabled": False}
    f.actor.guild_permissions = discord.Permissions.none()
    denied = click(f)
    await view._action(denied, "skip")
    assert "Чужі" in denied.followup.send.await_args.args[0]
    f.player.skip.assert_not_awaited()
    f.actor.guild_permissions = discord.Permissions(manage_guild=True)
    allowed = click(f)
    await view._action(allowed, "skip")
    f.player.skip.assert_awaited_once()
    await view.expire()


async def test_panel_stop_closes_view_only_after_manager_permission(invocation, monkeypatch):
    f = music_invocation
    await f.cog._ensure(f.guild, f.channel)
    view = await opened(f)
    remove = AsyncMock(return_value=True)
    monkeypatch.setattr(f.cog, "_remove", remove)
    f.actor.guild_permissions = discord.Permissions.none()
    await view._action(click(f), "stop")
    remove.assert_not_awaited()
    f.actor.guild_permissions = discord.Permissions(manage_guild=True)
    caller = click(f)
    await view._action(caller, "stop")
    remove.assert_awaited_once_with(f.guild.id)
    assert view.expired and view not in f.cog.panels
    assert all(child.disabled for child in view.children)
    caller.edit_original_response.assert_awaited_once()


async def test_too_many_panels_prevents_new_instance(invocation, monkeypatch):
    f = music_invocation
    await f.cog._ensure(f.guild, f.channel)
    monkeypatch.setattr(music_module, "MAX_PANELS", 1)
    view = await opened(f)
    f.interaction.followup.send.reset_mock()
    with pytest.raises(UserError, match="Ліміт"):
        await invoke(f, "panel")
    f.interaction.followup.send.assert_not_awaited()
    await view.expire()
