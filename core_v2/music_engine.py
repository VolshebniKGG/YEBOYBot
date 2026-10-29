"""Bounded per-guild music scheduling; Discord and FFmpeg live in the adapter.

Extractor work runs in a disposable isolated process, never an uncancellable
thread. No cookies, OAuth bypass, media downloads or shell commands are used.
"""
import asyncio
from collections import deque
from contextlib import redirect_stderr, redirect_stdout
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
        def __init__(self, message, *, code="source_unavailable"):
            super().__init__(message)
            self.code = code

log = logging.getLogger(__name__)
CLEANUP_RETRY_INTERVAL = 1
MAX_WORKER_INPUT = 16_384
MAX_WORKER_OUTPUT = 64_000

FAILURE_MESSAGES = {
    "network": "Мережа не дала отримати трек. Перевірте інтернет, DNS і налаштований proxy; спробуйте пізніше.",
    "tls": "Не підтверджено сертифікат джерела. Перевірте системний час і довірені сертифікати; не вимикайте TLS-перевірку.",
    "rate_limited": "Джерело тимчасово обмежило запити. Зачекайте або виберіть інший трек; автоматичні спроби мають паузу.",
    "source_unavailable": "Не вдалося отримати відкритий аудіопотік. Перевірте доступність треку або виберіть інший.",
    "login_required": "Джерело вимагає входу або додаткового доступу. Виберіть відкритий трек; бот не входить в облікові записи джерела.",
    "format_unavailable": "Джерело не надало підтримуваний аудіоформат. Зупиніть бот, оновіть бібліотеки через [5] або виберіть інший трек.",
    "runtime_missing": "Для YouTube не підтверджено JS runtime. Перевірте Deno або Node.js ≥20; Windows: [25] → [4], бібліотеки: [5].",
    "extractor_timeout": "Джерело не відповіло вчасно. Перевірте мережу або спробуйте інший трек.",
    "playback_error": "Відтворення перервано. Перевірте голосове з’єднання та FFmpeg через [25]; наступна спроба матиме паузу.",
}


def normalize_failure_code(value):
    """Never expose untrusted exception text or worker-provided codes."""
    return value if type(value) is str and value in FAILURE_MESSAGES else "source_unavailable"


def failure_message(code):
    return FAILURE_MESSAGES[normalize_failure_code(code)]


def classify_failure(message):
    """Best-effort category, without retaining or returning provider text."""
    if not isinstance(message, str):
        return "source_unavailable"
    text = message[:8192].lower()
    categories = (
        ("tls", ("certificate verify failed", "certificate_verify_failed", "ssl certificate", "tls handshake")),
        ("rate_limited", ("http error 429", "too many requests", "rate limit")),
        ("login_required", ("sign in", "login required", "log in to", "authentication required", "private video")),
        ("format_unavailable", ("requested format is not available", "no video formats", "no audio formats")),
        ("runtime_missing", ("no supported javascript runtime", "javascript runtime not found", "deno was not found")),
        ("extractor_timeout", ("timed out", "timeout error")),
        ("network", ("connection refused", "connection reset", "connection aborted", "network is unreachable",
                     "name or service not known", "name resolution", "getaddrinfo failed", "unable to connect",
                     "proxy error", "proxyerror", "remote end closed connection", "failed to establish a new connection")),
    )
    for code, patterns in categories:
        if any(pattern in text for pattern in patterns):
            return code
    return "source_unavailable"


class _ExtractorLogger:
    def __init__(self):
        self.warning_code = self.error_code = "source_unavailable"

    def debug(self, _message):
        pass

    def warning(self, message):
        code = classify_failure(message)
        if code != "source_unavailable":
            self.warning_code = code

    def error(self, message):
        code = classify_failure(message)
        if code != "source_unavailable":
            self.error_code = code

    def failure(self, error):
        if isinstance(error, (ImportError, ModuleNotFoundError)):
            return "runtime_missing"
        if isinstance(error, TimeoutError):
            return "extractor_timeout"
        argument = error.args[0] if error.args else None
        code = classify_failure(argument)
        if code != "source_unavailable":
            return code
        return self.error_code if self.error_code != "source_unavailable" else self.warning_code


class _DiscardOutput:
    """Provider print calls must never contaminate the one-shot JSON protocol."""
    def write(self, value):
        return len(value)

    def flush(self):
        pass


async def _worker_output(process, data):
    """Stream with a hard result cap rather than collecting unbounded stdout."""
    if len(data) > MAX_WORKER_INPUT:
        raise ValueError("Extractor request too large")
    try:
        process.stdin.write(data)
        await process.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        process.stdin.close()
    output = bytearray()
    while chunk := await process.stdout.read(8192):
        output.extend(chunk)
        if len(output) > MAX_WORKER_OUTPUT:
            raise ValueError("Extractor response too large")
    await process.wait()
    return bytes(output)


async def _reap_worker(process):
    """Kill only our owned child, then drain buffered output before wait()."""
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    # A paused stdout transport can keep Process.wait pending after kill.
    # Drain/discard in bounded chunks, never communicate()/retain the remainder.
    try:
        async with asyncio.timeout(5):
            while await process.stdout.read(8192):
                pass
            await process.wait()
    except (TimeoutError, OSError):
        # Closing this child's own transport releases pipes inherited by an
        # extractor runtime. It must not affect another process or transport.
        transport = getattr(process, "_transport", None)
        if transport is not None:
            transport.close()
        try:
            async with asyncio.timeout(1):
                await process.wait()
        except (TimeoutError, OSError):
            log.warning("Extractor child cleanup did not confirm exit")


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
        except ValueError:
            raise MusicError(failure_message("runtime_missing"), code="runtime_missing") from None
        process = None
        try:
            async with asyncio.timeout(self.timeout):
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-I", str(Path(__file__).resolve()), "--resolve",
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, limit=16_384)
                output = await _worker_output(process, json.dumps({"query": query, "js_runtimes": runtime}).encode())
                payload = json.loads(output)
                if not isinstance(payload, dict):
                    raise ValueError
                if set(payload) == {"error"}:
                    code = normalize_failure_code(payload["error"])
                    raise MusicError(failure_message(code), code=code)
                if process.returncode:
                    raise ValueError
                stream_url = validate_stream_url(payload["stream_url"])
                title = payload["title"]
                duration = payload["duration"]
                if (not isinstance(title, str) or not title or len(title) > 500 or
                        type(duration) not in (int, float) or not 0 < duration <= 14_400):
                    raise ValueError
                return Track(title, stream_url, source, float(duration))
        except asyncio.CancelledError:
            raise
        except MusicError as error:
            code = normalize_failure_code(error.code)
            raise MusicError(failure_message(code), code=code) from None
        except TimeoutError:
            raise MusicError(failure_message("extractor_timeout"), code="extractor_timeout") from None
        except (OSError, ValueError, TypeError, KeyError):
            raise MusicError(failure_message("source_unavailable")) from None
        finally:
            if process is not None:
                cleanup = asyncio.create_task(_reap_worker(process))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise


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
        self.error_code = None
        self._changed = asyncio.Event()
        self._interrupted = False
        self._closed = False
        self._ap_index = 0
        self._failures = 0
        self._cleaning = False
        self.manual_paused = False
        self._pause_started = None
        self._pause_seconds = 0.0
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

    async def set_manual_paused(self, enabled):
        """Change session intent atomically; an empty-channel pause is separate."""
        if type(enabled) is not bool:
            raise MusicError("Виберіть увімкнення або вимкнення ручної паузи.")
        if self._closed or self._task.done():
            raise MusicError("Музичний плеєр уже зупинено.")
        try:
            if self.backend.is_connected() is not True:
                raise MusicError("Голосове з'єднання відновлюється. Повторіть пізніше.")
            self.backend.set_manual_paused(enabled)
        except MusicError:
            raise
        except Exception:
            raise MusicError("Не вдалося змінити паузу. Перевірте /music queue і повторіть.") from None
        changed = self.manual_paused != enabled
        if changed:
            now = self.manager.clock()
            if enabled:
                self._pause_started = now
            else:
                self._pause_seconds += max(0.0, now - self._pause_started)
                self._pause_started = None
            self.manual_paused = enabled
            self._changed.set()
        return changed

    async def set_volume(self, percent):
        """Update only this backend's current and future PCM gain atomically."""
        if type(percent) is not int or not 0 <= percent <= 100:
            raise MusicError("Гучність має бути цілим числом від 0 до 100.")
        if self._closed or self._task.done():
            raise MusicError("Музичний плеєр уже зупинено.")
        try:
            if self.backend.is_connected() is not True:
                raise MusicError("Голосове з'єднання відновлюється. Повторіть пізніше.")
            changed = self.backend.set_volume(percent / 100)
            if type(changed) is not bool:
                raise ValueError
            return changed
        except Exception:
            raise MusicError("Не вдалося підтвердити зміну гучності. Перевірте /music queue і повторіть.") from None

    def _paused_seconds(self):
        running = (max(0.0, self.manager.clock() - self._pause_started)
                   if self._pause_started is not None else 0.0)
        return self._pause_seconds + running

    async def _wait_for_resume(self):
        while self.manual_paused and not self._closed and not self._interrupted:
            self.state = "paused"
            self._changed.clear()
            await self._changed.wait()
        return not self._closed and not self._interrupted

    def snapshot(self):
        return {"current": self.current, "request": self.request,
                "autoplay": bool(self.request and self.request.requester_id is None),
                "autoplay_enabled": self.autoplay_enabled, "queue": tuple(self.queue),
                "state": self.state, "error": self.error, "error_code": self.error_code,
                "manual_paused": self.manual_paused}

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
                if not await self._wait_for_resume():
                    continue
                self.request = self._next()
                if self.request is None:
                    self.state = "idle"
                    await self._changed.wait()
                    continue
                self.state = "resolving"
                self.error = None
                self.error_code = None
                failed = False
                try:
                    while True:
                        track = await self._until_changed(self._resolve(self.request.source))
                        if track is None or self._interrupted or not self.manual_paused:
                            break
                        if not await self._wait_for_resume():
                            track = None
                            break
                        # A signed URL may expire while paused. Resolve the same
                        # held request afresh; do not consume another queue item.
                        self.state = "resolving"
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

                    # Source lookup and waiting for an extractor slot are not
                    # playback. They must not hide an immediate successful EOF.
                    started = self.manager.clock()
                    paused_before = self._paused_seconds()
                    self.backend.play(track, finish)
                    result = await self._until_changed(finished)
                    elapsed = (self.manager.clock() - started
                               - (self._paused_seconds() - paused_before))
                    failed = result is not None or (not self._interrupted and
                                                    elapsed < 1)
                    if failed:
                        self.error_code = "playback_error"
                        self.error = failure_message(self.error_code)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    # Do not expose URLs, tokens, subprocess stderr or raw API errors.
                    self.error_code = (normalize_failure_code(error.code) if isinstance(error, MusicError)
                                       else "extractor_timeout" if isinstance(error, TimeoutError)
                                       else "playback_error")
                    self.error = failure_message(self.error_code)
                    failed = True
                    log.warning("Music track failed guild=%s code=%s", self.guild_id, self.error_code)
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
        raw = sys.stdin.buffer.read(MAX_WORKER_INPUT + 1)
        if len(raw) > MAX_WORKER_INPUT:
            raise ValueError
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        query = payload["query"]
        # Revalidate parent-provided input, rather than trust a command-line caller.
        if query.startswith("ytsearch1:"):
            query = validate_source(query[len("ytsearch1:"):])
        else:
            query = validate_source(query)
        if spotify_track_id(query):
            raise ValueError
    except Exception:
        raise SystemExit(1) from None
    logger = _ExtractorLogger()
    try:
        with redirect_stdout(_DiscardOutput()), redirect_stderr(_DiscardOutput()):
            import yt_dlp
            with yt_dlp.YoutubeDL({
                "format": "bestaudio/best", "noplaylist": True, "playlistend": 1,
                "quiet": True, "no_warnings": True, "noprogress": True, "logger": logger,
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
                result = json.dumps({"title": str(info.get("title") or "Трек")[:500],
                                     "stream_url": stream, "duration": duration})
        print(result)
    except Exception as error:
        print(json.dumps({"error": normalize_failure_code(logger.failure(error))}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    if sys.argv[1:] != ["--resolve"]:
        raise SystemExit(2)
    _extractor_worker()
