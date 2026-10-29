"""Session gain preserves scheduling, failure backoff and separate pause policy."""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core_v2.music_engine import MusicError, MusicManager, Track
from core_v2.music_options import MusicOptions
from core_v2.tests.test_music_volume_backend import pcm as pcm, samples


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


class Resolver:
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.failure = False

    async def resolve(self, source):
        self.calls.append(source)
        self.entered.set()
        await self.release.wait()
        if self.failure:
            raise MusicError("synthetic resolver failure")
        return Track(source, "https://a.googlevideo.com/audio", source, 180)


@pytest.fixture
async def session(pcm):
    clock, resolver = [0.0], Resolver()
    manager = MusicManager(MusicOptions(use_autoplay=False, autoplay_random=False),
                           ["automatic"], resolver, clock=lambda: clock[0])
    player = manager.attach(10, pcm.backend)
    yield SimpleNamespace(pcm=pcm, manager=manager, player=player, resolver=resolver,
                          backend=pcm.backend, voice=pcm.voice, clock=clock)
    await manager.close()


async def test_volume_is_idempotent_without_waking_worker_or_mutating_schedule(session):
    f = session
    await f.player.enqueue("first", 20)
    await f.player.enqueue("second", 21)
    await eventually(lambda: f.voice.starts == 1)
    f.player._failures = 3
    before = (f.player.current, f.player.request, tuple(f.player.queue), f.player.state,
              f.player._interrupted, f.player._changed.is_set(), f.player._ap_index,
              f.player._pause_started, f.player._pause_seconds)
    assert await f.player.set_volume(60) is True
    assert await f.player.set_volume(60) is False
    assert before == (f.player.current, f.player.request, tuple(f.player.queue), f.player.state,
                      f.player._interrupted, f.player._changed.is_set(), f.player._ap_index,
                      f.player._pause_started, f.player._pause_seconds)
    assert f.player._failures == 3 and not f.player.manual_paused
    assert samples(f.voice.source) == (6000, -6000)
    assert f.voice.starts == 1 and f.voice.stops == 0


async def test_muted_track_advances_through_user_skip_and_autoplay(session):
    f = session
    await f.player.set_autoplay(True)
    await f.player.enqueue("first", 20)
    await f.player.enqueue("second", 21)
    await eventually(lambda: f.voice.starts == 1)
    assert await f.player.set_volume(0) is True
    assert samples(f.voice.source) == (0, 0)
    assert await f.player.skip() is True
    await eventually(lambda: f.voice.starts == 2)
    assert f.player.current.title == "second" and samples(f.voice.source) == (0, 0)
    f.clock[0] = 180.0
    f.voice.playing = False
    f.voice.after(None)
    f.voice.source.cleanup()
    await eventually(lambda: f.voice.starts == 3)
    assert f.player.current.title == "automatic" and samples(f.voice.source) == (0, 0)
    assert f.player.manual_paused is False and f.backend._auto_paused is False
    assert f.resolver.calls == ["first", "second", "automatic"]


async def test_volume_while_resolving_only_sets_future_gain_and_does_not_restart_resolve(session):
    f = session
    f.resolver.release.clear()
    await f.player.enqueue("first", 20)
    await f.resolver.entered.wait()
    request = f.player.request
    assert f.player.state == "resolving" and f.voice.starts == 0
    changed_before = f.player._changed.is_set()
    assert await f.player.set_volume(75) is True
    assert f.player._changed.is_set() is changed_before and f.player.request is request
    assert f.backend.volume_state() == {"configured": .75, "actual": None, "source": "configured"}
    f.resolver.release.set()
    await eventually(lambda: f.voice.starts == 1)
    assert f.resolver.calls == ["first"] and samples(f.voice.source) == (7500, -7500)


async def test_manual_and_empty_channel_pause_remain_independent_from_volume(session):
    f = session
    await f.player.enqueue("first", 20)
    await f.player.enqueue("second", 21)
    await eventually(lambda: f.voice.starts == 1)
    await f.player.set_manual_paused(True)
    f.backend.set_paused(True)
    assert await f.player.set_volume(0) is True
    assert f.player.manual_paused and f.voice.paused and f.backend._auto_paused
    assert await f.player.skip() is True
    await eventually(lambda: f.player.state == "paused" and f.player.current is None)
    assert await f.player.set_volume(80) is True
    assert [r.source for r in f.player.queue] == ["second"] and f.voice.starts == 1
    await f.player.set_manual_paused(False)
    await eventually(lambda: f.voice.starts == 2)
    assert f.voice.paused and f.backend._auto_paused
    assert samples(f.voice.source) == (8000, -8000)
    f.backend.set_paused(False)
    assert f.voice.playing and not f.player.manual_paused


async def test_backoff_counter_and_waiter_are_not_reset_or_awakened_by_gain(session):
    f = session
    f.resolver.failure = True
    await f.player.set_autoplay(True)
    await eventually(lambda: f.player.state == "backoff")
    failures, calls = f.player._failures, list(f.resolver.calls)
    assert f.player._changed.is_set() is False
    assert await f.player.set_volume(10) is True
    assert f.player._changed.is_set() is False and f.player._failures == failures
    for _ in range(10):
        await asyncio.sleep(0)
    assert f.player.state == "backoff" and f.resolver.calls == calls


@pytest.mark.parametrize("value", [True, False, None, "60", 1.0, 60.5, float("nan"),
                                  float("inf"), float("-inf"), -1, 101, 10**1000, [], {}])
async def test_direct_engine_calls_strictly_validate_integer_percent_before_backend(session, value):
    f = session
    setter = MagicMock(wraps=f.backend.set_volume)
    f.backend.set_volume = setter
    with pytest.raises(MusicError):
        await f.player.set_volume(value)
    setter.assert_not_called()
    assert f.backend.volume == .25
    assert not f.player._changed.is_set() and not f.player.manual_paused


@pytest.mark.parametrize("condition", ["closed", "worker-done", "disconnected", "connection-unknown"])
async def test_unavailable_player_rejects_without_committing_gain(session, condition):
    f = session
    if condition == "closed":
        await f.player.close()
    elif condition == "worker-done":
        f.player._task.cancel()
        await asyncio.gather(f.player._task, return_exceptions=True)
    elif condition == "disconnected":
        f.voice.connected = False
    else:
        f.voice.connected = None
    setter = MagicMock(wraps=f.backend.set_volume)
    f.backend.set_volume = setter
    with pytest.raises(MusicError):
        await f.player.set_volume(80)
    setter.assert_not_called()
    assert f.backend.volume == .25


@pytest.mark.parametrize("outcome", [RuntimeError("https://private.invalid/secret"),
                                    MusicError("https://private.invalid/secret"), None, 1])
async def test_backend_error_or_unknown_outcome_never_leaks_private_errors(session, outcome):
    f = session
    f.backend.set_volume = MagicMock(side_effect=outcome if isinstance(outcome, Exception) else None,
                                    return_value=outcome)
    with pytest.raises(MusicError) as error:
        await f.player.set_volume(80)
    assert "private" not in str(error.value) and error.value.__cause__ is None
    assert f.backend.volume == .25 and not f.player._changed.is_set()


async def test_concurrent_updates_are_serial_and_do_not_restart_source(session):
    f = session
    await f.player.enqueue("first", 20)
    await eventually(lambda: f.voice.starts == 1)
    results = await asyncio.gather(*(f.player.set_volume(value) for value in [10, 90, 0, 100]))
    assert results == [True, True, True, True]
    assert f.backend.volume_state() == {"configured": 1, "actual": 1, "source": "active"}
    assert samples(f.voice.source) == (10000, -10000)
    assert f.voice.starts == 1 and f.voice.stops == 0 and not f.player.manual_paused


async def test_source_cleanup_rejects_gain_until_finalized_without_changing_queue(session):
    f = session
    await f.player.enqueue("first", 20)
    await f.player.enqueue("second", 21)
    await eventually(lambda: f.voice.starts == 1)
    source = f.voice.source
    source._cleanup_claimed = True
    before = tuple(f.player.queue)
    try:
        with pytest.raises(MusicError):
            await f.player.set_volume(80)
        assert tuple(f.player.queue) == before and f.backend.volume == .25
        assert f.player.current.title == "first"
    finally:
        source._cleanup_claimed = False
