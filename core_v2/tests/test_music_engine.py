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
                                   "https://open.spotify.com/track/1234567890123456789012?si=x"])
def test_single_spotify_track_reference_allowed_for_metadata_mapping(source):
    assert spotify_track_id(source) == "1234567890123456789012"
    assert validate_source(source) == source


@pytest.mark.parametrize("source", ["spotify:album:1234567890123456789012",
    "https://open.spotify.com/playlist/1234567890123456789012", "spotify:track:bad",
    "https://user:pass@open.spotify.com/track/1234567890123456789012",
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
