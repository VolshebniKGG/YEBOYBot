"""Manual session intent preserves FIFO, preemption and owned workers."""
import asyncio
from types import SimpleNamespace

import pytest

from core_v2.music_engine import MusicError, MusicManager, Track
from core_v2.music_options import MusicOptions


class Backend:
    def __init__(self):
        self.connected = True
        self.manual = False
        self.automatic = False
        self.played = []
        self.after = None
        self.failure = None

    def is_connected(self):
        return self.connected

    def set_manual_paused(self, value):
        if self.failure:
            raise self.failure
        self.manual = value

    def play(self, track, after):
        self.played.append(track.title)
        self.after = after

    def stop(self):
        pass


class Resolver:
    def __init__(self):
        self.calls = []

    async def resolve(self, source):
        self.calls.append(source)
        return Track(source, "https://a.googlevideo.com/audio", source, 180)


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


@pytest.fixture
async def music():
    clock = [0.0]
    resolver, backend = Resolver(), Backend()
    manager = MusicManager(MusicOptions(autoplay_random=False, use_autoplay=False), ["automatic"], resolver,
                           clock=lambda: clock[0])
    player = manager.attach(10, backend)
    yield SimpleNamespace(clock=clock, resolver=resolver, backend=backend, manager=manager, player=player)
    await manager.close()


async def test_pause_before_worker_starts_keeps_fifo_and_does_not_extract(music):
    f = music
    assert await f.player.set_manual_paused(True) is True
    await f.player.enqueue("first", 20)
    await f.player.enqueue("second", 21)
    await eventually(lambda: f.player.state == "paused")
    assert [r.source for r in f.player.queue] == ["first", "second"]
    assert f.resolver.calls == [] and f.backend.played == [] and f.player._ap_index == 0
    assert f.player.snapshot()["manual_paused"] is True
    assert await f.player.set_manual_paused(False) is True
    await eventually(lambda: f.backend.played == ["first"])
    assert [r.source for r in f.player.queue] == ["second"]


async def test_auto_selection_does_not_advance_while_manual_pause(music):
    f = music
    await f.player.set_manual_paused(True)
    await eventually(lambda: f.player.state == "paused")
    await f.player.set_autoplay(False)
    for _ in range(20):
        await asyncio.sleep(0)
    assert not f.resolver.calls and f.player._ap_index == 0
    await f.player.set_manual_paused(False)
    await eventually(lambda: f.player.state == "idle")
    assert f.player.manual_paused is False


async def test_repeat_controls_are_idempotent_and_apply_to_existing_track(music):
    f = music
    await f.player.set_autoplay(True)
    await eventually(lambda: f.player.state == "playing")
    request, current = f.player.request, f.player.current
    assert await f.player.set_manual_paused(True) is True
    assert await f.player.set_manual_paused(True) is False
    assert f.backend.manual is True
    assert f.player.request is request and f.player.current is current
    assert await f.player.set_manual_paused(False) is True
    assert await f.player.set_manual_paused(False) is False
    assert f.backend.manual is False and f.backend.played == ["automatic"]


async def test_skip_cannot_start_next_request_until_manual_resume(music):
    f = music
    await f.player.set_autoplay(False)
    await f.player.enqueue("first", 20)
    await f.player.enqueue("second", 20)
    await eventually(lambda: f.player.state == "playing")
    await f.player.set_manual_paused(True)
    assert await f.player.skip() is True
    await eventually(lambda: f.player.state == "paused" and f.player.request is None)
    assert f.backend.played == ["first"]
    assert [r.source for r in f.player.queue] == ["second"]
    await f.player.set_manual_paused(False)
    await eventually(lambda: f.backend.played == ["first", "second"])


async def test_user_preempts_paused_autoplay_but_waits_for_manual_resume(music):
    f = music
    await f.player.set_autoplay(True)
    await eventually(lambda: f.backend.played == ["automatic"])
    await f.player.set_manual_paused(True)
    await f.player.enqueue("requested", 20)
    await eventually(lambda: f.player.state == "paused" and f.player.request is None)
    assert f.backend.played == ["automatic"]
    assert [r.source for r in f.player.queue] == ["requested"]
    await f.player.set_manual_paused(False)
    await eventually(lambda: f.backend.played == ["automatic", "requested"])
    assert not f.player._failures


async def test_paused_resolution_holds_same_request_and_refreshes_url_on_resume(music):
    f = music
    entered, release = asyncio.Event(), asyncio.Event()
    original = f.resolver.resolve

    async def resolve(source):
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await original(source)

    f.resolver.resolve = resolve
    await f.player.set_autoplay(False)
    await f.player.enqueue("first", 20)
    await f.player.enqueue("second", 21)
    await entered.wait()
    held = f.player.request
    await f.player.set_manual_paused(True)
    release.set()
    await eventually(lambda: f.player.state == "paused")
    assert f.resolver.calls == ["first"] and not f.backend.played
    assert f.player.request is held and [r.source for r in f.player.queue] == ["second"]
    await f.player.set_manual_paused(False)
    await eventually(lambda: f.backend.played == ["first"])
    assert f.resolver.calls == ["first", "first"]


async def test_user_preemption_while_resolution_held_preserves_interrupt(music):
    f = music
    entered, release = asyncio.Event(), asyncio.Event()
    original = f.resolver.resolve

    async def resolve(source):
        entered.set()
        await release.wait()
        return await original(source)

    f.resolver.resolve = resolve
    await f.player.set_autoplay(True)
    await entered.wait()
    await f.player.set_manual_paused(True)
    release.set()
    await eventually(lambda: f.player.state == "paused")
    await f.player.enqueue("requested", 20)
    await eventually(lambda: f.player.request is None)
    assert not f.backend.played and [r.source for r in f.player.queue] == ["requested"]
    await f.player.set_manual_paused(False)
    await eventually(lambda: f.backend.played == ["requested"])
    assert f.resolver.calls == ["automatic", "requested"] and f.player._failures == 0


async def test_close_reaps_manually_paused_worker_and_clears_pending_queue(music):
    f = music
    await f.player.set_manual_paused(True)
    await f.player.enqueue("requested", 20)
    await eventually(lambda: f.player.state == "paused")
    await f.player.close()
    assert f.player._task.done() and not f.player.queue and f.player.state == "closed"
    with pytest.raises(MusicError, match="зупинено"):
        await f.player.set_manual_paused(False)


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}],
                         ids=["none", "zero", "one", "string", "list", "mapping"])
async def test_non_boolean_pause_intent_is_rejected_without_side_effect(music, value):
    f = music
    with pytest.raises(MusicError):
        await f.player.set_manual_paused(value)
    assert f.player.manual_paused is False and f.backend.manual is False


@pytest.mark.parametrize("enabled", [True, False], ids=["pause", "resume"])
async def test_disconnected_controls_preserve_existing_manual_intent(music, enabled):
    f = music
    await f.player.set_manual_paused(True)
    f.backend.connected = False
    with pytest.raises(MusicError, match="відновлюється"):
        await f.player.set_manual_paused(enabled)
    assert f.player.manual_paused is True and f.backend.manual is True


@pytest.mark.parametrize("enabled", [True, False], ids=["pause", "resume"])
async def test_backend_error_is_safe_and_does_not_commit_manual_intent(music, enabled):
    f = music
    await f.player.set_manual_paused(not enabled)
    f.backend.failure = RuntimeError("synthetic-private-url")
    with pytest.raises(MusicError) as result:
        await f.player.set_manual_paused(enabled)
    assert "synthetic-private" not in str(result.value)
    assert f.player.manual_paused is (not enabled)


async def test_unexpected_worker_exit_rejects_manual_control(music):
    f = music
    f.player._task.cancel()
    await asyncio.gather(f.player._task, return_exceptions=True)
    with pytest.raises(MusicError, match="зупинено"):
        await f.player.set_manual_paused(True)


async def test_long_manual_pause_does_not_mask_instant_eof_backoff(music):
    f = music
    await f.player.set_autoplay(True)
    await eventually(lambda: f.player.state == "playing")
    await f.player.set_manual_paused(True)
    f.clock[0] += 120
    await f.player.set_manual_paused(False)
    f.backend.after(None)
    await eventually(lambda: f.player.state == "backoff")
    assert f.player._failures == 1 and f.player.error_code == "playback_error"


async def test_healthy_elapsed_time_excludes_repeated_manual_pause_only_once(music):
    f = music
    await f.player.set_autoplay(False)
    await f.player.enqueue("requested", 20)
    f.player._failures = 3
    await eventually(lambda: f.player.state == "playing")
    f.clock[0] += .5
    await f.player.set_manual_paused(True)
    f.clock[0] += 60
    assert await f.player.set_manual_paused(True) is False
    f.clock[0] += 60
    await f.player.set_manual_paused(False)
    f.clock[0] += .5
    f.backend.after(None)
    await eventually(lambda: f.player.state == "idle")
    assert f.player._failures == 0 and f.player.error_code is None
    assert f.player._pause_seconds == 120


async def test_manual_pause_does_not_change_another_guild(music):
    f = music
    other_backend = Backend()
    other = f.manager.attach(11, other_backend)
    await other.set_autoplay(True)
    await f.player.set_manual_paused(True)
    await eventually(lambda: other.state == "playing")
    assert f.player.manual_paused is True and other.manual_paused is False
    assert not other_backend.manual and other_backend.played == ["automatic"]


async def test_idempotent_resume_does_not_wake_error_backoff(music):
    f = music

    async def unavailable(source):
        f.resolver.calls.append(source)
        raise MusicError("Синтетичний збій джерела.", code="network")

    f.resolver.resolve = unavailable
    await f.player.set_autoplay(True)
    await eventually(lambda: f.player.state == "backoff")
    assert f.resolver.calls == ["automatic"]
    assert await f.player.set_manual_paused(False) is False
    for _ in range(100):
        await asyncio.sleep(0)
    assert f.resolver.calls == ["automatic"] and f.player._failures == 1
