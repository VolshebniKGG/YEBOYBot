"""Turning autoplay off cancels only automatic live retries (no network/audio)."""
import asyncio

import pytest

from core_v2.music_engine import MusicManager, Track
from core_v2.music_options import MusicOptions


class Resolver:
    def __init__(self):
        self.calls = []

    async def resolve(self, source):
        self.calls.append(source)
        live = source != "listener song"
        return Track(source, "https://a.googlevideo.com/audio", source,
                     None if live else 180, live)


class Backend:
    def __init__(self):
        self.played = []
        self.after = None

    def is_connected(self):
        return True

    def play(self, track, after):
        self.played.append(track.title)
        self.after = after

    def stop(self):
        pass


async def eventually(predicate):
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("Music worker did not reach the expected state")


@pytest.fixture
async def setup():
    resolver = Resolver()
    manager = MusicManager(MusicOptions(autoplay_random=False), ["automatic live"], resolver)
    backend = Backend()
    player = manager.attach(1, backend)
    yield manager, resolver, backend, player
    await manager.close()


@pytest.mark.parametrize("error", [None, RuntimeError("private provider detail")])
async def test_disable_during_live_backoff_cancels_automatic_retry(setup, error):
    _, resolver, backend, player = setup
    await eventually(lambda: player.state == "playing")
    backend.after(error)
    await eventually(lambda: player.state == "backoff")
    assert player.request is None
    assert player._live_retry.requester_id is None

    await player.set_autoplay(False)
    await eventually(lambda: player.state == "idle")

    assert player._live_retry is None
    assert player._live_retry_count == 0
    assert resolver.calls == ["automatic live"]
    assert backend.played == ["automatic live"]


async def test_disable_preserves_user_requested_live_retry(setup):
    _, resolver, backend, player = setup
    await player.set_autoplay(False)
    await player.enqueue("requested live", 10)
    await eventually(lambda: player.state == "playing")
    backend.after(None)
    await eventually(lambda: player.state == "backoff")
    pending = player._live_retry
    initial_calls = list(resolver.calls)
    assert pending.requester_id == 10

    # Repeating the disable action must not remove the user's explicit request.
    await player.set_autoplay(False)
    await eventually(lambda: backend.played == ["requested live", "requested live"])

    assert resolver.calls == initial_calls + ["requested live"]
    assert player.request is pending
    assert player._live_retry_count == 1


async def test_disable_preserves_listener_song_but_removes_pending_auto_retry(setup):
    _, resolver, backend, player = setup
    await eventually(lambda: player.state == "playing")
    backend.after(None)
    await eventually(lambda: player.state == "backoff")
    await player.enqueue("listener song", 10)
    await eventually(lambda: backend.played == ["automatic live", "listener song"])
    current = player.current
    request = player.request
    assert player._live_retry.requester_id is None

    await player.set_autoplay(False)

    assert player._live_retry is None
    assert player.current is current
    assert player.request is request
    assert not player._interrupted
    assert resolver.calls == ["automatic live", "listener song"]
    # Skipping that song leaves nothing automatic to resurrect.
    await player.skip()
    await eventually(lambda: player.state == "idle")
    assert backend.played == ["automatic live", "listener song"]


async def test_disabling_one_guild_does_not_change_another_guild(setup):
    manager, _, backend, player = setup
    other_backend = Backend()
    other = manager.attach(2, other_backend)
    await eventually(lambda: player.state == other.state == "playing")
    backend.after(None)
    other_backend.after(None)
    await eventually(lambda: player.state == other.state == "backoff")
    other_pending = other._live_retry
    other_count = other._live_retry_count

    await player.set_autoplay(False)
    await eventually(lambda: player.state == "idle")

    assert other.autoplay_enabled
    assert other._live_retry is other_pending
    assert other._live_retry_count == other_count
    assert other.state == "backoff"


async def test_reenable_resolves_fresh_after_cancelled_auto_retry(setup):
    _, resolver, backend, player = setup
    await eventually(lambda: player.state == "playing")
    original_track = player.current
    backend.after(None)
    await eventually(lambda: player.state == "backoff")
    await player.set_autoplay(False)
    await eventually(lambda: player.state == "idle")

    await player.set_autoplay(True)
    await eventually(lambda: backend.played == ["automatic live", "automatic live"])

    assert resolver.calls == ["automatic live", "automatic live"]
    assert player.current is not original_track
    assert player._live_retry_count == 0
    assert not player._resuming_live
