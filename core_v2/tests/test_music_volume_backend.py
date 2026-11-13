"""Real PCM gain reads and owned-source lifecycle, without FFmpeg or audio login."""
import asyncio
import struct
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest

from core_v2 import music as module
from core_v2.music import FFmpegVoiceBackend, Music
from core_v2.music_engine import MusicError


class Samples(discord.AudioSource):
    def __init__(self):
        self.reads = 0
        self.cleanups = 0

    def read(self):
        self.reads += 1
        return struct.pack("<hh", 10000, -10000)

    def cleanup(self):
        # FFmpegPCMAudio cleanup is idempotent, including SDK destructors.
        self.cleanups = 1


class Voice:
    def __init__(self):
        self.source = None
        self.connected = True
        self.playing = False
        self.paused = False
        self.starts = 0
        self.stops = 0
        self.pauses = 0
        self.resumes = 0

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return self.playing

    def is_paused(self):
        return self.paused

    def play(self, source, *, after):
        self.source, self.after = source, after
        self.playing, self.paused = True, False
        self.starts += 1

    def stop(self):
        self.stops += 1
        self.playing = self.paused = False

    def pause(self):
        self.pauses += 1
        self.playing, self.paused = False, True

    def resume(self):
        self.resumes += 1
        self.playing, self.paused = True, False

    async def disconnect(self, **kwargs):
        self.connected = False


@pytest.fixture
async def pcm(monkeypatch):
    voice, sources = Voice(), []

    def create(*args, **kwargs):
        source = Samples()
        sources.append(source)
        return source

    factory = MagicMock(side_effect=create)
    monkeypatch.setattr(module.discord, "FFmpegPCMAudio", factory)
    backend = FFmpegVoiceBackend(voice, "unused", .25)
    fixture = SimpleNamespace(voice=voice, backend=backend, sources=sources, factory=factory,
                              track=SimpleNamespace(stream_url="https://a.googlevideo.com/audio"))
    yield fixture
    await backend.close()


def samples(source):
    return struct.unpack("<hh", source.read())


async def test_actual_pcm_gain_changes_current_source_without_respawn(pcm):
    f = pcm
    f.backend.play(f.track, MagicMock())
    owned = f.voice.source
    assert samples(owned) == (2500, -2500)
    assert f.backend.set_volume(.6) is True
    assert samples(owned) == (6000, -6000)
    assert f.backend.volume_state() == {"configured": .6, "actual": .6, "source": "active"}
    assert f.backend.set_volume(.6) is False
    assert f.voice.source is owned and f.voice.starts == 1
    assert f.voice.pauses == f.voice.resumes == f.voice.stops == 0
    f.factory.assert_called_once()


async def test_mute_returns_full_silent_frame_and_preserves_pause_intents(pcm):
    f = pcm
    f.backend.play(f.track, MagicMock())
    assert f.backend.set_volume(0) is True
    before = f.sources[0].reads
    assert samples(f.voice.source) == (0, 0)
    assert f.sources[0].reads == before + 1 and f.voice.playing is True
    assert not f.backend._manual_paused and not f.backend._auto_paused
    f.backend.set_manual_paused(True)
    f.backend.set_paused(True)
    assert f.backend.set_volume(.8) is True
    assert f.voice.paused and f.backend._manual_paused and f.backend._auto_paused
    assert samples(f.voice.source) == (8000, -8000)
    assert f.voice.pauses == 1 and f.voice.resumes == 0


async def test_new_tracks_reuse_session_gain_and_new_backend_resets_default(pcm):
    f = pcm
    f.backend.play(f.track, MagicMock())
    f.backend.set_volume(.7)
    f.backend.stop()
    assert await f.backend.wait_stopped() is True
    f.backend.play(f.track, MagicMock())
    assert samples(f.voice.source) == (7000, -7000)
    assert len(f.sources) == 2 and f.sources[0].cleanups == 1
    fresh = FFmpegVoiceBackend(Voice(), "unused", .325)
    assert fresh.volume_state() == {"configured": .325, "actual": None, "source": "configured"}
    assert "32.5%" in Music._volume_text(fresh)


async def test_idle_intent_applies_when_track_starts(pcm):
    assert pcm.backend.volume_state() == {"configured": .25, "actual": None, "source": "configured"}
    assert pcm.backend.set_volume(0) is True
    assert pcm.backend.set_volume(0.0) is False
    pcm.backend.play(pcm.track, MagicMock())
    assert samples(pcm.voice.source) == (0, 0)


INVALID = [True, False, None, "0.5", [], {}, float("nan"), float("inf"),
           float("-inf"), -.01, 1.01, 10**1000]


@pytest.mark.parametrize("value", INVALID)
async def test_backend_rejects_invalid_gain_before_current_or_future_mutation(pcm, value):
    pcm.backend.play(pcm.track, MagicMock())
    with pytest.raises(MusicError):
        pcm.backend.set_volume(value)
    assert pcm.backend.volume == .25 and samples(pcm.voice.source) == (2500, -2500)
    assert pcm.voice.starts == 1 and pcm.voice.stops == 0


@pytest.mark.parametrize("value", INVALID)
def test_constructor_rejects_invalid_fraction(value):
    with pytest.raises(MusicError):
        FFmpegVoiceBackend(Voice(), "unused", value)


@pytest.mark.parametrize("flag", ["_cleanup_claimed", "finished", "cleanup_failed"])
async def test_source_being_finished_or_cleaned_rejects_update_and_is_unknown(pcm, flag):
    pcm.backend.play(pcm.track, MagicMock())
    source = pcm.voice.source
    setattr(source, flag, True)
    try:
        with pytest.raises(MusicError):
            pcm.backend.set_volume(.8)
        assert pcm.backend.volume == source.source.volume == .25
        assert pcm.backend.volume_state()["source"] == "unknown"
    finally:
        setattr(source, flag, False)


@pytest.mark.parametrize("playing,paused", [(False, False), (True, True), (None, False),
                                          (False, None), (1, False), (False, 0)])
async def test_ended_or_unknown_sdk_state_never_commits_future_gain(pcm, playing, paused):
    pcm.backend.play(pcm.track, MagicMock())
    pcm.voice.playing, pcm.voice.paused = playing, paused
    with pytest.raises(MusicError):
        pcm.backend.set_volume(.8)
    assert pcm.backend.volume == pcm.voice.source.source.volume == .25
    assert pcm.backend.volume_state()["source"] == "unknown"


async def test_different_voice_source_is_not_mutated(pcm):
    pcm.backend.play(pcm.track, MagicMock())
    original = pcm.voice.source
    pcm.voice.source = Samples()
    with pytest.raises(MusicError):
        pcm.backend.set_volume(.8)
    assert original.source.volume == pcm.backend.volume == .25
    assert pcm.backend.volume_state()["actual"] is None


async def test_disconnected_and_closed_backend_preserve_gain(pcm):
    pcm.voice.connected = False
    with pytest.raises(MusicError):
        pcm.backend.set_volume(.8)
    assert pcm.backend.volume_state()["source"] == "unknown"
    pcm.voice.connected = True
    assert await pcm.backend.close() is True
    with pytest.raises(MusicError):
        pcm.backend.set_volume(.8)
    assert pcm.backend.volume == .25


class FailingGain(discord.PCMVolumeTransformer):
    failure = None

    @property
    def volume(self):
        if self.failure == "get":
            raise RuntimeError("https://private.invalid/secret")
        return self._volume

    @volume.setter
    def volume(self, value):
        if self.failure == "before":
            raise RuntimeError("https://private.invalid/secret")
        if self.failure != "ignore":
            self._volume = value
        if self.failure == "after":
            raise RuntimeError("https://private.invalid/secret")
        if self.failure == "readback":
            self.failure = "get"


class TransitionGain(discord.PCMVolumeTransformer):
    hook = None
    reads = 0
    transition_at = None
    setter_hook = None

    @property
    def volume(self):
        self.reads += 1
        value = self._volume
        if self.reads == self.transition_at:
            self.hook()
        return value

    @volume.setter
    def volume(self, value):
        self._volume = value
        if self.setter_hook is not None:
            self.setter_hook()


@pytest.mark.parametrize("transition", ["end", "replace"])
@pytest.mark.parametrize("phase", ["before", "readback", "setter", "query"])
async def test_lifecycle_change_during_property_access_is_rechecked_before_commit(pcm, transition, phase):
    pcm.backend.play(pcm.track, MagicMock())
    source = pcm.voice.source
    wrapped = TransitionGain(pcm.sources[0], volume=.25)
    source.source = wrapped

    def change():
        if transition == "end":
            pcm.voice.stop()
        else:
            pcm.voice.source = Samples()

    wrapped.hook = change
    wrapped.transition_at = 2 if phase == "readback" else 1
    if phase == "setter":
        wrapped.transition_at = None
        wrapped.setter_hook = change
    if phase == "query":
        assert pcm.backend.volume_state()["source"] == "unknown"
    else:
        with pytest.raises(MusicError):
            pcm.backend.set_volume(.8)
    assert pcm.backend.volume == .25
    assert wrapped._volume == (.25 if phase in {"before", "query"} else .8)
    assert pcm.backend.volume_state()["actual"] is None


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), -.1, 1.1])
async def test_unknown_transformer_gain_is_not_reported_or_used_as_confirmed_previous_gain(pcm, value):
    pcm.backend.play(pcm.track, MagicMock())
    pcm.voice.source.source._volume = value
    assert pcm.backend.volume_state() == {"configured": .25, "actual": None, "source": "unknown"}
    with pytest.raises(MusicError):
        pcm.backend.set_volume(.8)
    assert pcm.backend.volume == .25


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("probe", ["is_connected", "is_playing", "is_paused"])
async def test_probe_exceptions_remain_unknown_and_do_not_expose_private_diagnostics(pcm, active, probe):
    if active:
        pcm.backend.play(pcm.track, MagicMock())
    original = getattr(pcm.voice, probe)
    setattr(pcm.voice, probe, MagicMock(side_effect=RuntimeError("https://private.invalid/secret")))
    try:
        state = pcm.backend.volume_state()
        assert state == {"configured": .25, "actual": None, "source": "unknown"}
        with pytest.raises(MusicError) as error:
            pcm.backend.set_volume(.8)
        assert "private" not in str(error.value) and "private" not in str(state)
        assert pcm.backend.volume == .25
    finally:
        setattr(pcm.voice, probe, original)


async def test_foreign_live_sdk_source_cannot_be_treated_as_idle_configuration(pcm):
    pcm.voice.source, pcm.voice.playing = Samples(), True
    assert pcm.backend._source is None
    with pytest.raises(MusicError):
        pcm.backend.set_volume(.8)
    assert pcm.backend.volume_state()["source"] == "unknown" and pcm.backend.volume == .25


@pytest.mark.parametrize("failure", ["before", "after", "ignore", "get", "readback"])
async def test_sdk_failures_do_not_commit_future_gain_or_expose_errors(pcm, failure):
    pcm.backend.play(pcm.track, MagicMock())
    wrapped = FailingGain(pcm.sources[0], volume=.25)
    pcm.voice.source.source = wrapped
    wrapped.failure = failure
    with pytest.raises(MusicError) as error:
        pcm.backend.set_volume(.8)
    assert "private" not in str(error.value) and error.value.__cause__ is None
    assert pcm.backend.volume == .25
    state = pcm.backend.volume_state()
    if failure == "after":
        assert state == {"configured": .25, "actual": .8, "source": "active"}
        assert "80%" in Music._volume_text(pcm.backend) and "25%" in Music._volume_text(pcm.backend)
    elif failure in {"get", "readback"}:
        assert state["actual"] is None and state["source"] == "unknown"
    else:
        assert state["actual"] == .25
    assert "private" not in str(state)


async def test_cleanup_claim_is_seen_while_native_cleanup_runs_without_holding_lock(pcm):
    pcm.backend.play(pcm.track, MagicMock())
    entered, release = threading.Event(), threading.Event()
    raw = pcm.sources[0]

    def cleanup():
        entered.set()
        release.wait(2)
        raw.cleanups += 1

    raw.cleanup = cleanup
    source = pcm.voice.source
    thread = threading.Thread(target=source.cleanup)
    thread.start()
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        assert source._lock.acquire(blocking=False)
        source._lock.release()
        with pytest.raises(MusicError):
            pcm.backend.set_volume(.8)
        assert pcm.backend.volume_state()["source"] == "unknown"
        assert pcm.backend.volume == .25
    finally:
        release.set()
        await asyncio.to_thread(thread.join, 2)
    assert not thread.is_alive() and raw.cleanups == 1


async def test_pcm_audio_thread_can_read_complete_frames_during_event_loop_updates(pcm):
    pcm.backend.play(pcm.track, MagicMock())
    source, ready, done = pcm.voice.source, threading.Event(), threading.Event()
    observed = []

    def read():
        ready.set()
        while not done.is_set():
            observed.append(samples(source))

    thread = threading.Thread(target=read)
    thread.start()
    try:
        assert await asyncio.to_thread(ready.wait, 1)
        for index in range(101):
            pcm.backend.set_volume(index / 100)
            positive, negative = samples(source)
            assert abs(positive - index * 100) <= 1
            assert abs(negative + index * 100) <= 1
        assert pcm.backend.volume == 1
    finally:
        done.set()
        await asyncio.to_thread(thread.join, 2)
    assert not thread.is_alive() and observed
    assert all(len(frame) == 2 and abs(frame[0] + frame[1]) <= 1 for frame in observed)
