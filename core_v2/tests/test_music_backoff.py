"""Clock-controlled EOF/backoff regressions, without voice or network calls."""
import asyncio

import pytest

from core_v2.music_engine import MusicManager, Track
from core_v2.music_options import MusicOptions


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


class SlowResolver:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    async def resolve(self, source):
        self.calls.append(source)
        # Extraction took five seconds; no real-time sleep is necessary.
        self.clock.value += 5
        return Track(source, "https://a.googlevideo.com/audio", source, 180)


class Backend:
    def __init__(self, clock, *, eof_elapsed=None):
        self.clock = clock
        self.eof_elapsed = eof_elapsed
        self.played = []
        self.after = None

    def is_connected(self):
        return True

    def play(self, track, after):
        self.played.append(track.title)
        self.after = after
        if self.eof_elapsed is not None and track.title == "automatic":
            self.clock.value += self.eof_elapsed
            after(None)

    def stop(self):
        pass


async def eventually(predicate):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("music worker did not reach the expected state")


@pytest.fixture
async def setup():
    clock = Clock()
    resolver = SlowResolver(clock)
    manager = MusicManager(MusicOptions(autoplay_random=False), ["automatic"], resolver,
                           clock=clock)
    yield clock, resolver, manager
    await manager.close()


@pytest.mark.parametrize("eof_elapsed", [0, .25, .999])
async def test_slow_resolution_does_not_hide_immediate_eof(setup, eof_elapsed):
    clock, resolver, manager = setup
    backend = Backend(clock, eof_elapsed=eof_elapsed)
    player = manager.attach(1, backend)

    await eventually(lambda: player.state == "backoff")
    for _ in range(100):
        await asyncio.sleep(0)

    assert resolver.calls == ["automatic"]
    assert backend.played == ["automatic"]
    assert player._failures == 1
    assert player.snapshot()["error_code"] == "playback_error"


async def test_slow_resolution_and_instant_eof_preserve_previous_failure_count(setup):
    clock, _, manager = setup
    player = manager.attach(1, Backend(clock, eof_elapsed=0))
    player._failures = 3

    await eventually(lambda: player.state == "backoff")

    assert player._failures == 4
    assert player.snapshot()["error_code"] == "playback_error"


@pytest.mark.parametrize("play_elapsed", [1, 180])
async def test_normal_completion_after_slow_resolution_resets_failures(setup, play_elapsed):
    clock, _, manager = setup
    backend = Backend(clock)
    player = manager.attach(1, backend)
    await player.set_autoplay(False)
    player._failures = 3
    await player.enqueue("requested", 10)
    await eventually(lambda: player.state == "playing")

    clock.value += play_elapsed
    backend.after(None)
    await eventually(lambda: player.state == "idle")

    assert player._failures == 0
    assert player.snapshot()["error_code"] is None
    assert backend.played == ["requested"]


async def test_skip_after_slow_resolution_is_not_an_instant_eof_failure(setup):
    clock, _, manager = setup
    backend = Backend(clock)
    player = manager.attach(1, backend)
    await player.set_autoplay(False)
    await player.enqueue("requested", 10)
    await eventually(lambda: player.state == "playing")

    assert await player.skip()
    await eventually(lambda: player.state == "idle")

    assert player._failures == 0
    assert player.snapshot()["error_code"] is None


async def test_user_preempts_autoplay_without_false_failure_after_slow_resolution(setup):
    clock, _, manager = setup
    backend = Backend(clock)
    player = manager.attach(1, backend)
    await eventually(lambda: backend.played == ["automatic"])

    await player.enqueue("requested", 10)
    await eventually(lambda: backend.played == ["automatic", "requested"])

    assert player._failures == 0
    assert player.snapshot()["error_code"] is None
    assert player.current.title == "requested"


async def test_user_request_wakes_instant_eof_backoff_and_healthy_completion_resets_it(setup):
    clock, resolver, manager = setup
    backend = Backend(clock, eof_elapsed=0)
    player = manager.attach(1, backend)
    await eventually(lambda: player.state == "backoff")

    await player.enqueue("requested", 10)
    await eventually(lambda: player.current is not None and player.current.title == "requested")

    assert resolver.calls == ["automatic", "requested"]
    assert player._failures == 1
    assert player.state == "playing"
    await player.set_autoplay(False)
    clock.value += 180
    backend.after(None)
    await eventually(lambda: player.state == "idle")
    assert player._failures == 0


async def test_preempting_slow_autoplay_resolution_does_not_create_a_failure(setup):
    clock, _, manager = setup
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class Blocking(SlowResolver):
        async def resolve(self, source):
            if source == "automatic":
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            return await super().resolve(source)

    manager.resolver = Blocking(clock)
    backend = Backend(clock)
    player = manager.attach(1, backend)
    await asyncio.wait_for(started.wait(), 1)

    await player.enqueue("requested", 10)
    await eventually(lambda: backend.played == ["requested"])

    assert cancelled.is_set()
    assert player._failures == 0
    assert player.snapshot()["error_code"] is None
