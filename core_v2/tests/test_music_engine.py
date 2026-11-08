import asyncio
import json
from pathlib import Path
import sys

import pytest

from core_v2.music_engine import (MusicError, MusicManager, Track, YTDLPResolver,
                                  spotify_track_id, validate_source, validate_stream_url)
from core_v2.music_options import MusicOptions


@pytest.fixture(autouse=True)
def installed_runtime_boundary(monkeypatch):
    # Fake subprocess tests must not depend on a system JS runtime. Runtime
    # discovery itself is covered separately, with no installer execution.
    monkeypatch.setattr("core_v2.media_environment.javascript_runtime",
                        lambda: {"node": {"path": "/synthetic/node"}})


class Backend:
    def __init__(self):
        self.connected = True
        self.played = []
        self.callbacks = []
        self.stops = 0

    def is_connected(self):
        return self.connected

    def play(self, track, after):
        self.played.append(track)
        self.callbacks.append(after)

    def stop(self):
        self.stops += 1


class Resolver:
    def __init__(self):
        self.calls = []

    async def resolve(self, source):
        self.calls.append(source)
        return Track(source, "https://a.googlevideo.com/audio?id=1", source, 180)


async def eventually(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(.001)
    raise AssertionError("music worker did not reach expected state")


@pytest.fixture
async def music():
    manager = MusicManager(MusicOptions(autoplay_random=False), ["automatic"], Resolver())
    yield manager
    await manager.close()


@pytest.mark.parametrize("source,expected", [
    ("пісня назва", "ytsearch1:пісня назва"), (" Artist track ", "ytsearch1:Artist track"),
    ("https://www.youtube.com/watch?v=abc", "https://www.youtube.com/watch?v=abc"),
    ("https://music.youtube.com/watch?v=abc", "https://music.youtube.com/watch?v=abc"),
    ("https://youtu.be/abc", "https://youtu.be/abc"),
    ("https://soundcloud.com/artist/track", "https://soundcloud.com/artist/track")])
def test_allowed_sources(source, expected):
    assert validate_source(source) == expected


@pytest.mark.parametrize("source", [None, "", "a" * 501, "name\nargument", "file:///etc/passwd",
    "http://youtube.com/x", "https://youtube.com.evil.example/x", "https://127.0.0.1/x",
    "https://localhost/x", "https://user:password@youtube.com/x", "https://youtube.com:444/x",
    "https://youtube.com:bad/x", "ftp://youtube.com/x", "ytsearch100:track", "C:\\music.mp3",
    "/tmp/file.mp3", "https://example.org/track.mp3", "\\server\\share"])
def test_unsafe_sources_rejected(source):
    with pytest.raises(MusicError):
        validate_source(source)


@pytest.mark.parametrize("source", ["https://x.googlevideo.com/a", "https://x.sndcdn.com/a"])
def test_safe_streams(source):
    assert validate_stream_url(source) == source


@pytest.mark.parametrize("source", [None, "file:///etc/passwd", "https://127.0.0.1/a",
    "http://x.googlevideo.com/a", "https://googlevideo.com.evil.example/a", "https://youtube.com/a",
    "https://user:pass@x.googlevideo.com/a", "https://x.googlevideo.com:8443/a",
    "https://x.googlevideo.com/a\r\nAuthorization: bad", "https://x.googlevideo.com/" + "a" * 16_000],
    ids=["non-string", "file", "loopback", "insecure", "suffix-spoof", "wrong-cdn",
         "userinfo", "port", "header-injection", "oversized-stream-url"])
def test_unsafe_streams_rejected(source):
    with pytest.raises(MusicError):
        validate_stream_url(source)


@pytest.mark.parametrize("source", ["spotify:track:1234567890123456789012",
                                   "https://open.spotify.com/track/1234567890123456789012?si=x",
                                   "https://open.spotify.com/intl-uk/track/1234567890123456789012?si=x",
                                   "https://open.spotify.com/intl-de/track/1234567890123456789012/"])
def test_single_spotify_track_reference_allowed_for_metadata_mapping(source):
    assert spotify_track_id(source) == "1234567890123456789012"
    assert validate_source(source) == source


@pytest.mark.parametrize("source", ["spotify:album:1234567890123456789012",
    "https://open.spotify.com/playlist/1234567890123456789012", "spotify:track:bad",
    "https://user:pass@open.spotify.com/track/1234567890123456789012",
    "https://open.spotify.com/intl-uk/playlist/1234567890123456789012",
    "https://open.spotify.com/intl-uk/anything/track/1234567890123456789012",
    "https://open.spotify.com/intl-uk/../track/1234567890123456789012",
    "https://evil.spotify.com/track/1234567890123456789012"])
def test_spotify_nontracks_and_credentials_rejected(source):
    with pytest.raises(MusicError):
        validate_source(source)


async def test_user_interrupts_autoplay_and_autoplay_resumes(music):
    elapsed = [0.0]
    music.clock = lambda: elapsed[0]
    backend = Backend()
    player = music.attach(1, backend)
    await eventually(lambda: len(backend.played) == 1)
    assert player.snapshot()["autoplay"]
    await player.enqueue("requested", 12)
    await eventually(lambda: len(backend.played) == 2)
    assert backend.played[-1].title == "requested"
    assert not player.snapshot()["autoplay"]
    elapsed[0] += 180  # A real track completed; instant exits exercise backoff separately.
    backend.callbacks[-1](None)
    await eventually(lambda: len(backend.played) == 3)
    assert backend.played[-1].title == "automatic"
    assert player.snapshot()["autoplay"]


async def test_user_track_is_not_interrupted_by_next_user(music):
    backend = Backend()
    player = music.attach(1, backend)
    await player.enqueue("first", 12)
    await eventually(lambda: len(backend.played) == 1)
    stops = backend.stops
    await player.enqueue("second", 13)
    await asyncio.sleep(.01)
    assert len(backend.played) == 1 and backend.stops == stops
    assert player.snapshot()["queue"][0].source == "second"
    backend.callbacks[-1](None)
    await eventually(lambda: len(backend.played) == 2)
    assert backend.played[-1].title == "second"


async def test_stale_callback_cannot_finish_new_track(music):
    backend = Backend()
    player = music.attach(1, backend)
    await eventually(lambda: len(backend.played) == 1)
    old_callback = backend.callbacks[0]
    await player.enqueue("requested", 12)
    await eventually(lambda: len(backend.played) == 2)
    old_callback(RuntimeError("old error"))
    await asyncio.sleep(.01)
    assert player.current.title == "requested"
    assert len(backend.played) == 2


async def test_other_guild_queue_and_stop_are_isolated(music):
    one, two = Backend(), Backend()
    p1, p2 = music.attach(1, one), music.attach(2, two)
    await p1.enqueue("one", 11)
    await p2.enqueue("two", 22)
    await eventually(lambda: len(one.played) == len(two.played) == 1)
    await music.detach(1)
    assert p1.state == "closed" and p2.current.title == "two"
    assert 1 not in music.players and 2 in music.players


async def test_bounded_queue_and_invalid_input_do_not_preempt(music):
    backend = Backend()
    player = music.attach(1, backend)
    await eventually(lambda: player.state == "playing")
    for invalid in ("file:///private", "https://localhost/a"):
        with pytest.raises(MusicError):
            await player.enqueue(invalid, 11)
    assert not player._interrupted and not player.queue
    for n in range(50):
        assert await player.enqueue(f"track {n}", 11) == n + 1
    with pytest.raises(MusicError, match="50"):
        await player.enqueue("overflow", 11)
    assert len(player.queue) == 50


async def test_player_limit_and_duplicate_guild(music):
    music.attach(1, Backend())
    with pytest.raises(MusicError):
        music.attach(1, Backend())
    for guild in range(2, 21):
        music.attach(guild, Backend())
    with pytest.raises(MusicError, match="ліміт"):
        music.attach(21, Backend())
    assert len(music.players) == 20


async def test_skip_and_toggle_do_not_restart_after_close(music):
    backend = Backend()
    player = music.attach(1, backend)
    await eventually(lambda: len(backend.played) == 1)
    await player.set_autoplay(False)
    await eventually(lambda: player.state == "idle")
    assert not await player.skip()
    await player.enqueue("request", 11)
    await eventually(lambda: len(backend.played) == 2)
    assert await player.skip()
    await eventually(lambda: player.state == "idle")
    await player.close()
    await player.set_autoplay(True)
    await asyncio.sleep(.01)
    assert len(backend.played) == 2 and player.state == "closed"
    with pytest.raises(MusicError):
        await player.enqueue("new", 11)


async def test_autoskip_can_be_disabled():
    backend = Backend()
    manager = MusicManager(MusicOptions(autoplay_auto_skip=False), ["auto"], Resolver())
    try:
        player = manager.attach(1, backend)
        await eventually(lambda: player.state == "playing")
        await player.enqueue("request", 11)
        await asyncio.sleep(.01)
        assert len(backend.played) == 1 and player.current.title == "auto"
        backend.callbacks[-1](None)
        await eventually(lambda: len(backend.played) == 2)
        assert player.current.title == "request"
    finally:
        await manager.close()


async def test_resolving_autoplay_is_cancelled_and_reaped_before_user(music):
    started, cancelled = asyncio.Event(), asyncio.Event()

    class Blocking(Resolver):
        async def resolve(self, source):
            if source == "automatic":
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            assert cancelled.is_set()
            return await super().resolve(source)

    music.resolver = Blocking()
    backend = Backend()
    player = music.attach(1, backend)
    await started.wait()
    await player.enqueue("user", 11)
    await eventually(lambda: len(backend.played) == 1)
    assert cancelled.is_set() and backend.played[0].title == "user"


async def test_repeated_autoplay_failure_uses_backoff_but_user_wakes_it(music):
    class Failing(Resolver):
        async def resolve(self, source):
            if source == "automatic":
                self.calls.append(source)
                raise RuntimeError("private raw exception")
            return await super().resolve(source)

    music.resolver = Failing()
    backend = Backend()
    player = music.attach(1, backend)
    await eventually(lambda: player.state == "backoff")
    await asyncio.sleep(.02)
    assert music.resolver.calls == ["automatic"]
    assert "private" not in player.error
    await player.enqueue("works", 11)
    await eventually(lambda: len(backend.played) == 1)
    assert player.current.title == "works"


async def test_immediate_playback_failures_back_off(music):
    class Instant(Backend):
        def play(self, track, after):
            super().play(track, after)
            after(RuntimeError("private ffmpeg error"))

    backend = Instant()
    player = music.attach(1, backend)
    await eventually(lambda: player.state == "backoff")
    await asyncio.sleep(.02)
    assert len(backend.played) == 1 and "private" not in player.error


async def test_transport_reconnect_preserves_worker_and_waiting_user_queue(music):
    elapsed = [0.0]
    music.clock = lambda: elapsed[0]
    backend = Backend()
    player = music.attach(1, backend)
    await eventually(lambda: player.state == "playing")
    await player.enqueue("first-user", 11)
    await eventually(lambda: player.current is not None and player.current.title == "first-user")
    await player.enqueue("already-waiting", 12)
    backend.connected = False
    elapsed[0] += 180
    backend.callbacks[-1](None)
    await eventually(lambda: player.state == "reconnecting")
    assert not player._task.done() and len(player.queue) == 1
    backend.connected = True
    await player.enqueue("new-after-reconnect", 13)
    await eventually(lambda: player.current is not None and player.current.title == "already-waiting")
    assert not player._task.done()
    assert [request.source for request in player.queue] == ["new-after-reconnect"]


async def test_pending_native_cleanup_preserves_next_request_in_queue(music, monkeypatch):
    monkeypatch.setattr("core_v2.music_engine.CLEANUP_RETRY_INTERVAL", .001)
    class Cleaning(Backend):
        cleaning = False

        async def wait_stopped(self, timeout=10):
            return not self.cleaning

    backend = Cleaning()
    player = music.attach(1, backend)
    await eventually(lambda: player.state == "playing")
    backend.cleaning = True
    await player.enqueue("do-not-drop", 11)
    await eventually(lambda: player.state == "cleanup")
    assert player.queue[0].source == "do-not-drop" and len(backend.played) == 1
    backend.cleaning = False
    await eventually(lambda: player.current is not None and player.current.title == "do-not-drop")


async def test_global_resolver_concurrency_and_close_cancellation(music):
    class Blocking:
        active = peak = 0

        async def resolve(self, source):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.Future()
            finally:
                self.active -= 1

    resolver = Blocking()
    music.resolver = resolver
    for guild in range(1, 4):
        music.attach(guild, Backend())
    await eventually(lambda: resolver.active == 2)
    assert resolver.peak == 2
    await music.close()
    assert resolver.active == 0 and not music.players


class Process:
    def __init__(self, payload=None, *, block=False, code=0):
        self.returncode = None
        self.payload, self.block, self.code = payload, block, code
        self.killed = self.waited = False
        self.input = None
        self.stdout = asyncio.StreamReader()
        process = self

        class Input:
            def write(self, data):
                process.input = data
                if not process.block:
                    process.stdout.feed_data(json.dumps(process.payload).encode())
                    process.stdout.feed_eof()
                    process.returncode = process.code

            async def drain(self):
                pass

            def close(self):
                pass

        self.stdin = Input()

    async def communicate(self, data):
        self.input = data
        if self.block:
            await asyncio.Future()
        self.returncode = self.code
        return json.dumps(self.payload).encode(), b""

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.stdout.feed_eof()

    async def wait(self):
        self.waited = True
        return self.returncode


async def test_resolver_uses_isolated_child_and_stdin_without_shell(monkeypatch):
    process = Process({"title": "Name", "stream_url": "https://a.googlevideo.com/a", "duration": 180})
    calls = []

    async def create(*args, **kwargs):
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    track = await YTDLPResolver().resolve("title")
    assert track.title == "Name" and track.duration == 180
    assert calls[0][0][0:2] == (sys.executable, "-I")
    assert Path(calls[0][0][2]).is_absolute()
    assert calls[0][0][-1] == "--resolve" and len(calls[0][0]) == 4
    assert json.loads(process.input)["query"] == "ytsearch1:title"
    assert calls[0][1]["stderr"] == asyncio.subprocess.DEVNULL


@pytest.mark.parametrize("payload", [{"title": "name", "stream_url": "file:///private", "duration": 1},
    {"title": "name", "stream_url": "https://a.googlevideo.com/a", "duration": 0},
    {"title": "name", "stream_url": "https://a.googlevideo.com/a", "duration": 14_401},
    {"title": "name", "stream_url": "https://a.googlevideo.com/a", "duration": None}, {}])
async def test_resolver_invalid_result_safe_error(monkeypatch, payload):
    async def create(*args, **kwargs):
        return Process(payload)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    with pytest.raises(MusicError, match="Не вдалося"):
        await YTDLPResolver().resolve("name")


async def test_resolver_timeout_kills_and_waits_process(monkeypatch):
    process = Process(block=True)
    async def create(*args, **kwargs):
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    with pytest.raises(MusicError, match="вчасно"):
        await YTDLPResolver(timeout=.01).resolve("name")
    assert process.killed and process.waited


async def test_resolver_cancel_kills_and_waits_process(monkeypatch):
    process = Process(block=True)
    started = asyncio.Event()
    async def create(*args, **kwargs):
        started.set()
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    task = asyncio.create_task(YTDLPResolver().resolve("name"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed and process.waited


async def test_spotify_missing_credentials_does_not_start_extractor(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("must not start yt-dlp")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(MusicError, match="credentials"):
        await YTDLPResolver().resolve("spotify:track:1234567890123456789012")


async def test_spotify_metadata_is_searched_not_played_directly(monkeypatch):
    class Metadata:
        async def search_query(self, source):
            assert source.startswith("spotify:track:")
            return "Artist Track"
    process = Process({"title": "Matched Track", "stream_url": "https://a.googlevideo.com/a", "duration": 180})
    async def create(*args, **kwargs):
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    track = await YTDLPResolver(spotify=Metadata()).resolve("spotify:track:1234567890123456789012")
    assert json.loads(process.input)["query"] == "ytsearch1:Artist Track"
    assert track.page_url.startswith("spotify:track:")


async def test_actual_private_worker_rejects_file_before_importing_extractor():
    from core_v2 import music_engine
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", str(Path(music_engine.__file__)), "--resolve",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, error = await process.communicate(b'{"query":"file:///private"}')
    assert process.returncode == 1 and out == error == b""


async def test_close_clears_state_even_if_backend_stop_raises():
    class BrokenStop(Backend):
        def stop(self):
            self.stops += 1
            raise RuntimeError("simulated voice adapter failure")

    backend = BrokenStop()
    manager = MusicManager(MusicOptions(autoplay_random=False), [], Resolver())
    try:
        player = manager.attach(123, backend)
        await player.enqueue("queued track", 11)
        await player.close()
        assert player.state == "closed"
        assert player.current is None
        assert player.request is None
        assert not player.queue
        assert player._task.done()
        await player.close()  # Repeated shutdown must remain safe.
    finally:
        await manager.close()

async def test_remove_queued_track_checks_owner_without_interrupting_playback(music):
    backend = Backend()
    player = music.attach(1, backend)
    await player.enqueue("first", 11)
    await eventually(lambda: player.current is not None and player.current.title == "first")
    await player.enqueue("second", 22)
    await player.enqueue("third", 11)
    stops, tracks = backend.stops, len(backend.played)

    with pytest.raises(MusicError, match="власне замовлення"):
        await player.remove(1, 11)
    assert [item.source for item in player.snapshot()["queue"]] == ["second", "third"]

    assert await player.remove(2, 11) == 1
    assert await player.remove(1, 11, manage_guild=True) == 0
    assert player.snapshot()["queue"] == ()
    assert player.current.title == "first"
    assert backend.stops == stops and len(backend.played) == tracks


async def test_remove_rejects_invalid_indices_and_cannot_remove_active_track(music):
    backend = Backend()
    player = music.attach(1, backend)
    await player.enqueue("first", 11)
    await eventually(lambda: player.current is not None and player.current.title == "first")
    await player.enqueue("queued", 22)

    for position in (0, -1, 2, 51, True, 1.5):
        with pytest.raises(MusicError, match="позиції"):
            await player.remove(position, 22)
    for actor, manager in ((True, False), (0, False), (22, 1)):
        with pytest.raises(MusicError, match="права"):
            await player.remove(1, actor, manage_guild=manager)
    assert [item.source for item in player.snapshot()["queue"]] == ["queued"]

    assert await player.remove(1, 22) == 0
    with pytest.raises(MusicError, match="позиції"):
        await player.remove(1, 11, manage_guild=True)
    assert player.current.title == "first"

async def test_failed_backend_stop_quarantines_worker_and_queue(caplog):
    class BrokenStop(Backend):
        def stop(self):
            self.stops += 1
            raise RuntimeError("private-voice-url")

    backend = BrokenStop()
    manager = MusicManager(MusicOptions(autoplay_random=False), [], Resolver())
    try:
        player = manager.attach(7, backend)
        await player.enqueue("song", 11)
        await eventually(lambda: player.current is not None)
        backend.callbacks[-1](None)
        await eventually(player._task.done)
        assert player.state == "disconnected"
        assert player._task.exception() is None
        with pytest.raises(MusicError, match="недоступний"):
            await player.enqueue("another", 11)
        with pytest.raises(MusicError, match="недоступний"):
            await player.set_autoplay(True)
        assert "private-voice-url" not in caplog.text
    finally:
        await manager.close()

async def test_shuffle_only_waiting_requests_and_forces_nonidentity(music, monkeypatch):
    from core_v2 import music_engine as engine
    backend = Backend()
    player = music.attach(1, backend)
    await player.enqueue("playing-now", 11)
    await eventually(lambda: player.current is not None and player.current.title == "playing-now")
    for index in range(5):
        await player.enqueue(f"waiting-{index}", index + 20)
    original = tuple(player.queue)
    before_stops, before_plays = backend.stops, len(backend.played)
    monkeypatch.setattr(engine.random, "shuffle", lambda items: None)
    assert await player.shuffle_queue() == 5
    after = tuple(player.queue)
    assert len(after) == len(original) and {id(x) for x in after} == {id(x) for x in original}
    assert any(a is not b for a, b in zip(after, original))
    assert player.current.title == "playing-now"
    assert backend.stops == before_stops and len(backend.played) == before_plays


async def test_shuffle_no_op_small_queue_and_closed_player(music):
    backend = Backend()
    player = music.attach(1, backend)
    await player.enqueue("playing", 11)
    await eventually(lambda: player.current is not None and player.current.title == "playing")
    assert await player.shuffle_queue() == 0
    await player.enqueue("only-waiting", 22)
    first = player.queue[0]
    assert await player.shuffle_queue() == 0
    assert player.queue[0] is first
    await player.close()
    with pytest.raises(MusicError, match="зупинено"):
        await player.shuffle_queue()


async def test_shuffle_isolated_across_guild_queues(music):
    a, b = Backend(), Backend()
    left = music.attach(1, a)
    right = music.attach(2, b)
    await left.enqueue("left-active", 11)
    await right.enqueue("right-active", 22)
    await eventually(lambda: left.current is not None and right.current is not None)
    for index in range(3):
        await left.enqueue(f"L-{index}", 11)
        await right.enqueue(f"R-{index}", 22)
    before_right = tuple(right.queue)
    assert await left.shuffle_queue() == 3
    assert tuple(right.queue) == before_right
    assert right.current.title == "right-active"

async def test_move_reorders_only_waiting_requests_without_playback(music):
    backend = Backend()
    player = music.attach(1, backend)
    await player.enqueue("current", 11)
    await eventually(lambda: player.current is not None and player.current.title == "current")
    for name in ("a", "b", "c", "d"):
        await player.enqueue(name, 22)
    stops, played = backend.stops, len(backend.played)
    assert await player.move(1, 4) is True
    assert [item.source for item in player.queue] == ["b", "c", "d", "a"]
    assert await player.move(4, 2) is True
    assert [item.source for item in player.queue] == ["b", "a", "c", "d"]
    assert await player.move(2, 2) is False
    assert player.current.title == "current"
    assert backend.stops == stops and len(backend.played) == played


async def test_move_rejects_invalid_indices_and_closed_worker(music):
    backend = Backend()
    player = music.attach(1, backend)
    await player.enqueue("current", 11)
    await eventually(lambda: player.current is not None and player.current.title == "current")
    await player.enqueue("waiting", 22)
    for from_position, to_position in ((0, 1), (1, 0), (2, 1), (1, 2),
                                       (True, 1), (1, False), (1.5, 1), (1, "1")):
        with pytest.raises(MusicError, match="Позиції"):
            await player.move(from_position, to_position)
    assert len(player.queue) == 1
    await player.close()
    with pytest.raises(MusicError, match="зупинено"):
        await player.move(1, 1)


async def test_move_other_guild_does_not_change_queue(music):
    left, right = music.attach(1, Backend()), music.attach(2, Backend())
    await left.enqueue("active-left", 11)
    await right.enqueue("active-right", 22)
    await eventually(lambda: left.current is not None and right.current is not None)
    for name in ("a", "b", "c"):
        await left.enqueue(name, 11)
        await right.enqueue(name, 22)
    before = tuple(right.queue)
    assert await left.move(3, 1)
    assert tuple(right.queue) == before

async def test_repeat_one_plays_completed_user_track_again_then_skip_advances(music):
    clock = [0.0]
    music.clock = lambda: clock[0]
    backend = Backend()
    player = music.attach(1, backend)
    await player.set_repeat("one")
    await player.enqueue("again", 11)
    await eventually(lambda: player.current is not None and player.current.title == "again")
    await player.enqueue("next", 22)
    clock[0] += 10
    backend.callbacks[-1](None)
    await eventually(lambda: len(backend.played) == 2)
    assert backend.played[-1].title == "again"
    assert [item.source for item in player.queue] == ["next"]
    assert player.snapshot()["repeat_mode"] == "one"
    assert await player.skip()
    await eventually(lambda: len(backend.played) == 3)
    assert backend.played[-1].title == "next"
    await player.set_repeat("off")
    assert player.snapshot()["repeat_mode"] == "off"


async def test_repeat_all_cycles_without_growing_queue(music):
    clock = [0.0]
    music.clock = lambda: clock[0]
    backend = Backend()
    player = music.attach(1, backend)
    await player.set_repeat("all")
    for name in ("A", "B", "C"):
        await player.enqueue(name, 11)
    await eventually(lambda: len(backend.played) == 1)
    for index, expected in enumerate(("A", "B", "C", "A", "B", "C")):
        await eventually(lambda: len(backend.played) == index + 1)
        assert backend.played[-1].title == expected
        assert len(player.queue) <= 2
        clock[0] += 10
        backend.callbacks[-1](None)
    await player.set_repeat("off")
    assert player.snapshot()["repeat_mode"] == "off"
    assert len(player.queue) <= 3


async def test_repeat_never_requeues_failed_or_skipped_user_track(music):
    clock = [0.0]
    music.clock = lambda: clock[0]
    backend = Backend()
    player = music.attach(1, backend)
    await player.set_repeat("all")
    await player.enqueue("bad", 11)
    await eventually(lambda: player.current is not None and player.current.title == "bad")
    backend.callbacks[-1](RuntimeError("private"))
    await eventually(lambda: player.state == "backoff")
    assert not player.queue
    await player.enqueue("skip-me", 22)
    await eventually(lambda: player.current is not None and player.current.title == "skip-me")
    assert await player.skip()
    await eventually(lambda: player.state in {"idle", "playing", "resolving"})
    assert not player.queue


async def test_repeat_rejects_invalid_values_and_stopped_player(music):
    player = music.attach(1, Backend())
    for invalid in (None, True, 0, "forever", "", "all "):
        with pytest.raises(MusicError, match="Повтор"):
            await player.set_repeat(invalid)
    await player.close()
    with pytest.raises(MusicError, match="зупинено"):
        await player.set_repeat("one")


async def test_enqueue_next_preserves_active_audio_and_waiting_order(music):
    backend = Backend()
    player = music.attach(1, backend)
    await player.enqueue("active", 11)
    await eventually(lambda: player.current is not None and player.current.title == "active")
    for name in ("a", "b"):
        await player.enqueue(name, 22)
    before = (backend.stops, len(backend.played))
    assert await player.enqueue_next("next-track", 44) == 1
    assert [item.source for item in player.queue] == ["next-track", "a", "b"]
    assert player.current.title == "active"
    assert (backend.stops, len(backend.played)) == before


async def test_clear_queue_does_not_stop_active_stream_or_voice(music):
    backend = Backend()
    player = music.attach(1, backend)
    await player.enqueue("active", 11)
    await eventually(lambda: player.current is not None)
    for name in ("a", "b", "c"):
        await player.enqueue(name, 22)
    before = (backend.stops, len(backend.played))
    assert await player.clear_queue() == 3
    assert await player.clear_queue() == 0
    assert tuple(player.queue) == ()
    assert player.current.title == "active"
    assert (backend.stops, len(backend.played)) == before
    assert await player.enqueue("new", 33) == 1


async def test_jump_places_selected_waiting_track_next_without_discarding_others(music):
    backend = Backend()
    player = music.attach(1, backend)
    await player.enqueue("active", 11)
    await eventually(lambda: player.current is not None and player.current.title == "active")
    for name in ("first", "second", "target", "fourth"):
        await player.enqueue(name, 22)
    assert await player.jump(3) == 22
    assert [item.source for item in player.queue] == ["target", "first", "second", "fourth"]
    await eventually(lambda: len(backend.played) >= 2)
    assert backend.played[-1].title == "target"
    assert [item.source for item in player.queue] == ["first", "second", "fourth"]


async def test_jump_preempts_repeat_one_without_replaying_skipped_audio(music):
    backend = Backend()
    player = music.attach(1, backend)
    await player.set_repeat("one")
    await player.enqueue("active", 11)
    await eventually(lambda: player.current is not None and player.current.title == "active")
    await player.enqueue("target", 22)
    await player.enqueue("remaining", 33)
    assert await player.jump(1) == 22
    await eventually(lambda: len(backend.played) == 2)
    assert [track.title for track in backend.played] == ["active", "target"]
    assert [item.source for item in player.queue] == ["remaining"]


async def test_jump_clear_next_validation_and_closed_state(music):
    backend = Backend()
    player = music.attach(1, backend)
    await player.enqueue("active", 11)
    await eventually(lambda: player.current is not None and player.current.title == "active")
    await player.enqueue("waiting", 22)
    for position in (0, -1, 2, 50, True, False, 1.0, "1"):
        with pytest.raises(MusicError, match="позиції|позиція"):
            await player.jump(position)
    for value in (None, "yes", 1):
        with pytest.raises(MusicError, match="порядок"):
            await player.enqueue("other", 22, next_up=value)
    assert [item.source for item in player.queue] == ["waiting"]
    await player.close()
    with pytest.raises(MusicError, match="зупинено"):
        await player.clear_queue()
    with pytest.raises(MusicError, match="зупинено"):
        await player.jump(1)
    with pytest.raises(MusicError, match="недоступний"):
        await player.enqueue_next("after-close", 22)


async def test_next_clear_jump_are_isolated_by_guild(music):
    left, right = music.attach(1, Backend()), music.attach(2, Backend())
    await left.enqueue("active-L", 11)
    await right.enqueue("active-R", 22)
    await eventually(lambda: left.current is not None and right.current is not None)
    for name in ("a", "b", "c"):
        await left.enqueue(name, 11)
        await right.enqueue(name, 22)
    before = tuple(right.queue)
    await left.enqueue_next("new-first", 11)
    assert tuple(right.queue) == before
    assert await left.clear_queue() == 4
    assert tuple(right.queue) == before
    assert await right.jump(2) == 22
    assert tuple(left.queue) == ()
