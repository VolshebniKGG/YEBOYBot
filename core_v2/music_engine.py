"""Bounded per-guild music scheduling; Discord and FFmpeg live in the adapter.

Extractor work runs in a disposable isolated process, never an uncancellable
thread. No cookies, OAuth bypass, media downloads or shell commands are used.
"""
import asyncio
from collections import deque
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import random
import re
import sys
import time
from urllib.parse import urlsplit

# The same file is the isolated extractor entrypoint, without importing Core.
if __package__:
    from .music_options import MusicError, MusicOptions
else:
    class MusicError(ValueError):
        pass

log = logging.getLogger(__name__)
CLEANUP_RETRY_INTERVAL = 1


@dataclass(frozen=True)
class Track:
    title: str
    stream_url: str
    page_url: str
    duration: float | None = None


@dataclass(frozen=True)
class TrackRequest:
    source: str
    requester_id: int | None


def _host_is(host: str, allowed: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in allowed)


def spotify_track_id(source: str) -> str | None:
    """Accept individual public track references; never playlists or account URLs."""
    if source.lower().startswith("spotify:"):
        match = re.fullmatch(r"spotify:track:([A-Za-z0-9]{22})", source)
        if not match:
            raise MusicError("Spotify: підтримується посилання на один трек, не альбом чи плейлист.")
        return match[1]
    try:
        url = urlsplit(source)
        if _host_is((url.hostname or "").lower(), ("spotify.com",)):
            match = re.fullmatch(r"/track/([A-Za-z0-9]{22})/?", url.path)
            if (url.scheme != "https" or url.hostname != "open.spotify.com" or
                    url.username is not None or url.password is not None or
                    url.port not in (None, 443) or not match):
                raise MusicError("Spotify: підтримується HTTPS посилання open.spotify.com на один трек.")
            return match[1]
    except ValueError as error:
        if isinstance(error, MusicError):
            raise
        raise MusicError("Некоректне посилання на трек.") from None
    return None


def validate_source(source: str) -> str:
    """Only known public extractors, or a bounded YouTube text search."""
    if (not isinstance(source, str) or not source.strip() or len(source) > 500 or
            any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in source)):
        raise MusicError("Вкажіть назву або посилання до 500 символів без керівних знаків.")
    source = source.strip()
    if spotify_track_id(source):
        return source
    if "://" not in source:
        # Treat extractor pseudo-URLs and local file paths as invalid, not API instructions.
        if ":" in source or source.startswith(("/", "\\")):
            raise MusicError("Використайте назву треку або HTTPS YouTube/SoundCloud.")
        return "ytsearch1:" + source
    try:
        url = urlsplit(source)
        host = (url.hostname or "").lower()
        if (url.scheme != "https" or url.username is not None or url.password is not None or
                url.port not in (None, 443) or not _host_is(host, (
                    "youtube.com", "youtu.be", "soundcloud.com"))):
            raise ValueError
    except ValueError as error:
        if isinstance(error, MusicError):
            raise
        raise MusicError("Підтримуються HTTPS YouTube/SoundCloud або пошук за назвою.") from None
    return source


def validate_stream_url(value: str) -> str:
    """Never pass arbitrary protocols, hosts or credentials to FFmpeg."""
    try:
        url = urlsplit(value)
        if (not isinstance(value, str) or len(value) > 16_000 or
                any(ord(c) < 32 or ord(c) == 127 for c in value) or
                url.scheme != "https" or url.username is not None or url.password is not None or
                url.port not in (None, 443) or
                not _host_is((url.hostname or "").lower(), ("googlevideo.com", "sndcdn.com"))):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise MusicError("Джерело не повернуло дозволений захищений аудіопотік.") from None
    return value


class YTDLPResolver:
    def __init__(self, *, timeout=35, spotify=None):
        self.timeout = timeout
        self.spotify = spotify

    async def resolve(self, source: str) -> Track:
        query = validate_source(source)
        if spotify_track_id(source):
            if self.spotify is None:
                raise MusicError("Для Spotify потрібні власні API credentials у config/options.ini. Аудіо шукається на YouTube.")
            query = validate_source(await self.spotify.search_query(source))
            if not query.startswith("ytsearch1:"):
                raise MusicError("Не вдалося сформувати пошук за метаданими Spotify.")
        from .media_environment import javascript_runtime
        try:
            runtime = javascript_runtime()
        except ValueError as error:
            raise MusicError(str(error)) from None
        process = None
        try:
            async with asyncio.timeout(self.timeout):
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-I", str(Path(__file__).resolve()), "--resolve",
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL)
                output, _ = await process.communicate(json.dumps({"query": query, "js_runtimes": runtime}).encode())
                if process.returncode or len(output) > 64_000:
                    raise ValueError
                payload = json.loads(output)
                stream_url = validate_stream_url(payload["stream_url"])
                title = payload["title"]
                duration = payload["duration"]
                if (not isinstance(title, str) or not title or len(title) > 500 or
                        type(duration) not in (int, float) or not 0 < duration <= 14_400):
                    raise ValueError
                return Track(title, stream_url, source, float(duration))
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise MusicError("Джерело не відповіло вчасно. Спробуйте інший трек.") from None
        except (OSError, ValueError, TypeError, KeyError):
            raise MusicError("Не вдалося отримати відкритий аудіопотік. Спробуйте інший трек.") from None
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()


class MusicManager:
    """One worker/player per guild, a shared limit of two extractor processes."""
    def __init__(self, options: "MusicOptions", autoplaylist: list[str], resolver=None, *, clock=time.monotonic):
        self.options = options
        if len(autoplaylist) > 10000:
            raise MusicError("Автоплейлист перевищує 10000 записів.")
        self.autoplaylist = tuple(autoplaylist)
        self.clock = clock
        self.resolver = resolver or YTDLPResolver()
        self.players = {}
        self._slots = asyncio.Semaphore(2)
        self._closed = False

    def attach(self, guild_id, backend):
        if (self._closed or type(guild_id) is not int or not 0 < guild_id < 2**63 or
                guild_id in self.players):
            raise MusicError("Музичний плеєр уже підключений або недоступний.")
        if len(self.players) >= self.options.max_players:
            raise MusicError("Досягнуто ліміт одночасних музичних плеєрів.")
        player = GuildPlayer(self, guild_id, backend)
        self.players[guild_id] = player
        return player

    async def detach(self, guild_id):
        player = self.players.pop(guild_id, None)
        if player:
            await player.close()

    async def close(self):
        self._closed = True
        players = list(self.players.values())
        self.players.clear()
        await asyncio.gather(*(p.close() for p in players))


class GuildPlayer:
    def __init__(self, manager, guild_id, backend):
        self.manager, self.guild_id, self.backend = manager, guild_id, backend
        self.options = manager.options
        self.autoplay_enabled = self.options.use_autoplay
        self.queue = deque()
        self.current = None
        self.request = None
        self.state = "idle"
        self.error = None
        self._changed = asyncio.Event()
        self._interrupted = False
        self._closed = False
        self._ap_index = 0
        self._failures = 0
        self._cleaning = False
        self._task = asyncio.create_task(self._run(), name=f"music-{guild_id}")

    async def enqueue(self, source, requester_id):
        validate_source(source)
        if self._closed or not self.backend.is_connected():
            raise MusicError("Спочатку підключіть бота до голосового каналу.")
        if type(requester_id) is not int or not 0 < requester_id < 2**63:
            raise MusicError("Не вдалося визначити автора замовлення.")
        if len(self.queue) >= self.options.max_queue:
            raise MusicError("Черга заповнена: максимум 50 замовлень на сервер.")
        self.queue.append(TrackRequest(source.strip(), requester_id))
        if self.request and self.request.requester_id is None and self.options.autoplay_auto_skip:
            self._interrupted = True
        self._changed.set()
        return len(self.queue)

    async def skip(self):
        if self._closed or self.request is None:
            return False
        self._interrupted = True
        self._changed.set()
        return True

    async def set_autoplay(self, enabled):
        if type(enabled) is not bool:
            raise MusicError("Виберіть увімкнення або вимкнення автоплейлиста.")
        self.autoplay_enabled = enabled
        if not enabled and self.request and self.request.requester_id is None:
            self._interrupted = True
        self._changed.set()

    def snapshot(self):
        return {"current": self.current, "request": self.request,
                "autoplay": bool(self.request and self.request.requester_id is None),
                "autoplay_enabled": self.autoplay_enabled, "queue": tuple(self.queue),
                "state": self.state, "error": self.error}

    async def close(self):
        self._closed = True
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self.backend.stop()
        self.queue.clear()
        self.current = self.request = None
        self.state = "closed"

    def _next(self):
        if self.queue:
            return self.queue.popleft()
        items = self.manager.autoplaylist
        if self.autoplay_enabled and items:
            if self.options.autoplay_random:
                source = random.choice(items)
            else:
                source = items[self._ap_index % len(items)]
                self._ap_index += 1
            return TrackRequest(source, None)
        return None

    async def _until_changed(self, operation):
        """Cancel and reap extraction immediately when an auto track is preempted."""
        task = asyncio.ensure_future(operation)
        waiter = None
        try:
            while True:
                waiter = asyncio.create_task(self._changed.wait())
                done, _ = await asyncio.wait((task, waiter), return_when=asyncio.FIRST_COMPLETED)
                if waiter in done:
                    self._changed.clear()
                    if self._interrupted or self._closed:
                        return None
                if task in done:
                    return await task
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
        finally:
            if waiter:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _resolve(self, source):
        async with self.manager._slots:
            async with asyncio.timeout(40):
                return await self.manager.resolver.resolve(source)

    async def _backoff(self):
        self.state = "backoff"
        self._failures += 1
        delay = min(60, 2 ** min(self._failures, 6))
        self._changed.clear()
        # A waiting user track gets priority; persistent autoplay failures sleep.
        if self.queue:
            return
        try:
            await asyncio.wait_for(self._changed.wait(), timeout=delay)
        except TimeoutError:
            pass

    async def _stop(self):
        self.backend.stop()
        wait = getattr(self.backend, "wait_stopped", None)
        if wait is not None:
            self._cleaning = not await wait()

    async def _run(self):
        try:
            while not self._closed:
                if self._cleaning:
                    self.state = "cleanup"
                    self._cleaning = not await self.backend.wait_stopped(timeout=1)
                    if self._cleaning:
                        await asyncio.sleep(CLEANUP_RETRY_INTERVAL)
                        continue
                if not self.backend.is_connected():
                    # VoiceClient owns transport reconnect. Retain the queue and
                    # worker while it reconnects; do not accept a zombie session.
                    self.state = "reconnecting"
                    self._changed.clear()
                    try:
                        await asyncio.wait_for(self._changed.wait(), timeout=5)
                    except TimeoutError:
                        pass
                    continue
                self._interrupted = False
                self._changed.clear()
                self.request = self._next()
                if self.request is None:
                    self.state = "idle"
                    await self._changed.wait()
                    continue
                self.state = "resolving"
                self.error = None
                failed = False
                started = self.manager.clock()
                try:
                    track = await self._until_changed(self._resolve(self.request.source))
                    if track is None or self._interrupted:
                        continue
                    # Injected resolvers cannot bypass the FFmpeg URL policy either.
                    validate_stream_url(track.stream_url)
                    self.current = track
                    self.state = "playing"
                    loop = asyncio.get_running_loop()
                    finished = loop.create_future()

                    def finish(error, future=finished):
                        def deliver():
                            if not future.done():
                                future.set_result(error)
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(deliver)

                    self.backend.play(track, finish)
                    result = await self._until_changed(finished)
                    failed = result is not None or (not self._interrupted and
                                                    self.manager.clock() - started < 1)
                    if failed:
                        self.error = "Відтворення перервано джерелом; наступна спроба з паузою."
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Do not expose URLs, tokens, subprocess stderr or raw API errors.
                    self.error = "Не вдалося відтворити трек. Спробуйте інший."
                    failed = True
                    log.warning("Music track failed guild=%s", self.guild_id)
                finally:
                    await self._stop()
                    self.current = self.request = None
                if failed:
                    await self._backoff()
                elif not self._interrupted:
                    self._failures = 0
        except asyncio.CancelledError:
            raise
        finally:
            self.backend.stop()
            self.current = self.request = None
            self.state = "closed" if self._closed else "disconnected"


def _extractor_worker():
    """Private one-shot process: bounded input/output and no raw diagnostics."""
    try:
        payload = json.loads(sys.stdin.buffer.read(16384))
        query = payload["query"]
        # Revalidate parent-provided input, rather than trust a command-line caller.
        if query.startswith("ytsearch1:"):
            query = validate_source(query[len("ytsearch1:"):])
        else:
            query = validate_source(query)
        if spotify_track_id(query):
            raise ValueError
        import yt_dlp
        with yt_dlp.YoutubeDL({
                "format": "bestaudio/best", "noplaylist": True, "playlistend": 1,
                "quiet": True, "no_warnings": True, "noprogress": True,
                "socket_timeout": 10, "retries": 1, "extractor_retries": 1,
                "cachedir": False, "skip_download": True, "usenetrc": False,
                "nocheckcertificate": False, "js_runtimes": payload.get("js_runtimes", {})}) as extractor:
            info = extractor.extract_info(query, download=False)
        if "entries" in info:
            info = next(entry for entry in info["entries"] if entry)
        stream = validate_stream_url(info["url"])
        duration = info.get("duration")
        if info.get("is_live") or type(duration) not in (int, float) or not 0 < duration <= 14_400:
            raise ValueError
        print(json.dumps({"title": str(info.get("title") or "Трек")[:500],
                          "stream_url": stream, "duration": duration}))
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    if sys.argv[1:] != ["--resolve"]:
        raise SystemExit(2)
    _extractor_worker()
