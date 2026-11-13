"""Combined pause policy against real SDK AudioPlayer events, without threads/network."""
from unittest.mock import MagicMock

import discord
import pytest

from core_v2.music import FFmpegVoiceBackend
from core_v2.music_engine import MusicError
from core_v2.tests.test_music_commands import backend as backend, track


class SilentSource(discord.AudioSource):
    def read(self):
        return b""


@pytest.fixture
def sdk():
    voice = object.__new__(discord.VoiceClient)
    player = discord.player.AudioPlayer(SilentSource(), voice)
    player._speak = MagicMock()  # No websocket, thread, voice login or native media.
    voice._player = player
    backend = FFmpegVoiceBackend(voice, "unused", .25)
    yield voice, player, backend
    player.stop()


@pytest.mark.parametrize("manual", [False, True], ids=["manual-off", "manual-on"])
@pytest.mark.parametrize("automatic", [False, True], ids=["empty-off", "empty-on"])
def test_real_sdk_pause_is_combined_intent_without_starting_audio_thread(sdk, manual, automatic):
    voice, player, backend = sdk
    backend.set_paused(automatic)
    backend.set_manual_paused(manual)
    assert voice.is_paused() is (manual or automatic)
    assert voice.is_playing() is not (manual or automatic)
    assert backend.pause_state() == {"manual": manual, "automatic": automatic,
        "paused": manual or automatic, "playing": not (manual or automatic)}
    assert backend._paused is (manual or automatic)
    assert not player.is_alive()


def test_human_arrival_does_not_cancel_manual_pause(sdk):
    voice, _, backend = sdk
    backend.set_paused(True)
    backend.set_manual_paused(True)
    backend.set_paused(False)
    assert voice.is_paused() is True
    assert backend.pause_state()["manual"] is True
    assert backend.pause_state()["automatic"] is False


def test_manual_resume_does_not_override_empty_channel_pause(sdk):
    voice, _, backend = sdk
    backend.set_manual_paused(True)
    backend.set_paused(True)
    backend.set_manual_paused(False)
    assert voice.is_paused() is True
    assert backend.pause_state()["manual"] is False
    assert backend.pause_state()["automatic"] is True
    backend.set_paused(False)
    assert voice.is_playing() is True


def test_repeated_controls_do_not_toggle_sdk_or_cancel_other_pause(sdk):
    voice, player, backend = sdk
    backend.set_manual_paused(True)
    backend.set_manual_paused(True)
    backend.set_paused(True)
    backend.set_paused(True)
    backend.set_manual_paused(False)
    assert voice.is_paused() is True
    assert player._speak.call_count == 1
    backend.set_paused(False)
    backend.set_paused(False)
    backend.set_manual_paused(False)
    assert voice.is_playing() is True and player._speak.call_count == 2


@pytest.mark.parametrize("method", ["set_paused", "set_manual_paused"])
@pytest.mark.parametrize("value", [None, 0, 1, "false", [], {}],
                         ids=["none", "zero", "one", "string", "list", "mapping"])
def test_invalid_pause_values_do_not_mutate_sdk_or_policy(sdk, method, value):
    voice, player, backend = sdk
    with pytest.raises(MusicError):
        getattr(backend, method)(value)
    assert voice.is_playing() is True
    assert backend._paused is False and backend._manual_paused is False and backend._auto_paused is False
    player._speak.assert_not_called()


@pytest.mark.parametrize("method", ["pause", "resume"])
def test_sdk_exception_is_sanitized_and_logical_intent_is_not_committed(sdk, method):
    voice, _, backend = sdk
    if method == "resume":
        backend.set_manual_paused(True)
    setattr(voice, method, MagicMock(side_effect=RuntimeError("synthetic-private-stream")))
    with pytest.raises(MusicError) as error:
        backend.set_manual_paused(method == "pause")
    assert "synthetic-private" not in str(error.value)
    assert backend._manual_paused is (method == "resume")


def test_partial_sdk_pause_failure_remains_observable_without_false_rollback_claim(sdk):
    voice, _, backend = sdk
    original = voice.pause

    def partial_pause():
        original()
        raise RuntimeError("synthetic-private-audio")

    voice.pause = partial_pause
    with pytest.raises(MusicError):
        backend.set_manual_paused(True)
    state = backend.pause_state()
    assert state["manual"] is False and state["paused"] is True and state["playing"] is False


@pytest.mark.parametrize("method", ["is_paused", "is_playing"])
def test_sdk_probe_exception_is_unknown_and_contains_no_private_error(sdk, method):
    voice, _, backend = sdk
    setattr(voice, method, MagicMock(side_effect=RuntimeError("synthetic-private-path")))
    state = backend.pause_state()
    key = "paused" if method == "is_paused" else "playing"
    assert state[key] is None and "synthetic-private" not in str(state)


@pytest.mark.parametrize("value", [None, 0, 1, "false"], ids=["none", "zero", "one", "string"])
def test_non_boolean_sdk_state_is_unknown(sdk, value):
    voice, _, backend = sdk
    voice.is_paused = MagicMock(return_value=value)
    voice.is_playing = MagicMock(return_value=value)
    state = backend.pause_state()
    assert state["paused"] is None and state["playing"] is None


async def test_manual_pause_intent_is_applied_to_new_owned_source(backend):
    backend.backend.set_manual_paused(True)
    backend.backend.play(track(), MagicMock())
    backend.voice.pause.assert_called_once()
    assert backend.backend._paused is True
    assert await backend.backend.close() is True


async def test_closing_backend_rejects_manual_control(backend):
    assert await backend.backend.close() is True
    with pytest.raises(MusicError, match="завершує"):
        backend.backend.set_manual_paused(True)
