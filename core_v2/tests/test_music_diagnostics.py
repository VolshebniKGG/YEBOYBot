"""Bounded, secret-safe extraction protocol; real subprocesses stay offline."""
import asyncio
from io import BytesIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from core_v2 import music_engine as module
from core_v2.music_engine import (FAILURE_MESSAGES, MusicError, MusicManager, YTDLPResolver,
                                  classify_failure, failure_message, normalize_failure_code)
from core_v2.music_options import MusicOptions


@pytest.fixture(autouse=True)
def runtime(monkeypatch):
    monkeypatch.setattr("core_v2.media_environment.javascript_runtime",
                        lambda: {"node": {"path": "/synthetic/node"}})


@pytest.mark.parametrize("message,code", [
    ("certificate verify failed while establishing connection", "tls"),
    ("SSL: CERTIFICATE_VERIFY_FAILED", "tls"),
    ("HTTP Error 429: Too Many Requests", "rate_limited"),
    ("Sign in to confirm access", "login_required"),
    ("This is a private video", "login_required"),
    ("Requested format is not available", "format_unavailable"),
    ("No supported JavaScript runtime could be found", "runtime_missing"),
    ("read timed out while opening connection", "extractor_timeout"),
    ("Unable to connect: name resolution failed", "network"),
    ("Remote end closed connection without response", "network"),
    ("ProxyError: synthetic user:password@proxy", "network"),
    ("Video unavailable", "source_unavailable"),
    (None, "source_unavailable"), (123, "source_unavailable"),
    ("x" * 8192 + "certificate verify failed", "source_unavailable"),
], ids=["certificate", "certificate-symbol", "429", "signin", "private", "format", "runtime",
        "timeout", "dns", "remote-close", "proxy", "unavailable", "none", "number", "bounded-text"])
def test_failure_categories_are_fixed_and_specific_before_generic(message, code):
    assert classify_failure(message) == code
    assert failure_message(code) == FAILURE_MESSAGES[code]
    assert "password" not in failure_message(code)


@pytest.mark.parametrize("code", [None, 12, [], {}, "synthetic-token", "network\nsecret", "NETWORK"],
                         ids=["none", "number", "list", "dict", "unknown", "newline", "uppercase"])
def test_unknown_codes_never_reach_display(code):
    assert normalize_failure_code(code) == "source_unavailable"
    assert failure_message(code) == FAILURE_MESSAGES["source_unavailable"]


class Process:
    def __init__(self, payload, returncode=1):
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(json.dumps(payload).encode())
        self.stdout.feed_eof()
        self.returncode = returncode
        self.waited = self.killed = False
        self.stdin = SimpleNamespace(write=lambda data: None, drain=self.drain, close=lambda: None)

    async def drain(self):
        pass

    async def wait(self):
        self.waited = True
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


@pytest.mark.asyncio
@pytest.mark.parametrize("code", list(FAILURE_MESSAGES), ids=list(FAILURE_MESSAGES))
async def test_resolver_preserves_only_allowlisted_failure_code_and_fixed_message(monkeypatch, code):
    process = Process({"error": code})
    async def spawn(*args, **kwargs):
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(MusicError) as caught:
        await YTDLPResolver().resolve("пісня")
    assert caught.value.code == code
    assert str(caught.value) == failure_message(code)
    assert process.waited and not process.killed


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"error": "https://synthetic-token@example.test"}, {"error": {"secret": "synthetic-token"}},
    {"error": "network", "message": "synthetic-token"}, ["synthetic-token"], "synthetic-token", None,
    {"title": "synthetic-token", "stream_url": "https://x.googlevideo.com/a", "duration": 1},
], ids=["unknown", "nested", "extra-key", "list", "string", "null", "success-with-failed-exit"])
async def test_malformed_failure_envelopes_do_not_expose_child_payload(monkeypatch, payload):
    async def spawn(*args, **kwargs):
        return Process(payload)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(MusicError) as caught:
        await YTDLPResolver().resolve("title")
    assert caught.value.code == "source_unavailable"
    assert "synthetic-token" not in str(caught.value)


@pytest.mark.asyncio
async def test_missing_runtime_has_safe_code_without_spawning_worker(monkeypatch):
    def missing():
        raise ValueError("synthetic-token/private-path")
    async def forbidden(*args, **kwargs):
        raise AssertionError("worker must not start")
    monkeypatch.setattr("core_v2.media_environment.javascript_runtime", missing)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(MusicError) as caught:
        await YTDLPResolver().resolve("title")
    assert caught.value.code == "runtime_missing"
    assert "synthetic-token" not in str(caught.value)


def worker(monkeypatch, payload, *, message=None, failure=None, noise=True, lazy=False):
    observed = {}
    class Extractor:
        def __init__(self, options):
            observed.update(options)
            self.log = options["logger"]
        def __enter__(self):
            if noise:
                print("synthetic-secret on enter")
                print("synthetic-secret on stderr", file=sys.stderr)
            return self
        def __exit__(self, *args):
            if noise:
                print("synthetic-secret on exit")
        def extract_info(self, query, download):
            assert download is False
            if message:
                self.log.warning(message)
                self.log.error(message)
            if failure:
                raise failure
            if lazy:
                def entries():
                    print("synthetic-secret lazy metadata")
                    yield {"title": "Пісня", "url": "https://x.googlevideo.com/a", "duration": 180}
                return {"entries": entries()}
            return {"title": "Пісня", "url": "https://x.googlevideo.com/a", "duration": 180}
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=Extractor))
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO(json.dumps(payload).encode())))
    return observed


def test_worker_suppresses_provider_stdout_stderr_and_emits_only_code(monkeypatch, capsys):
    options = worker(monkeypatch, {"query": "title"},
        message="HTTP Error 429: synthetic-secret proxy://user:password@host", failure=ValueError("synthetic-secret"))
    with pytest.raises(SystemExit) as caught:
        module._extractor_worker()
    output = capsys.readouterr()
    assert caught.value.code == 1
    assert json.loads(output.out) == {"error": "rate_limited"}
    assert "synthetic-secret" not in output.out and output.err == ""
    assert options["nocheckcertificate"] is False and options["usenetrc"] is False


def test_worker_success_still_has_no_provider_noise(monkeypatch, capsys):
    worker(monkeypatch, {"query": "title"})
    module._extractor_worker()
    output = capsys.readouterr()
    assert json.loads(output.out) == {"title": "Пісня", "stream_url": "https://x.googlevideo.com/a", "duration": 180}
    assert output.err == "" and "synthetic-secret" not in output.out


def test_lazy_metadata_is_consumed_inside_suppressed_provider_context(monkeypatch, capsys):
    worker(monkeypatch, {"query": "title"}, lazy=True)
    module._extractor_worker()
    output = capsys.readouterr()
    assert json.loads(output.out)["title"] == "Пісня"
    assert "synthetic-secret" not in output.out and output.err == ""


def test_actual_yt_dlp_initializes_with_discarded_channels_without_network():
    import yt_dlp
    from contextlib import redirect_stderr, redirect_stdout
    logger = module._ExtractorLogger()
    with redirect_stdout(module._DiscardOutput()), redirect_stderr(module._DiscardOutput()):
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "logger": logger,
                              "cachedir": False, "usenetrc": False, "nocheckcertificate": False}) as extractor:
            # report_warning must still reach the safe logger with no_warnings.
            extractor.report_warning("HTTP Error 429 synthetic-secret")
    assert logger.warning_code == "rate_limited"


@pytest.mark.parametrize("payload", [b"[]", b"null", b"invalid", b"x" * (module.MAX_WORKER_INPUT + 1)],
                         ids=["list", "null", "malformed", "oversized"])
def test_worker_bad_input_rejected_before_extractor_without_payload_echo(monkeypatch, capsys, payload):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO(payload)))
    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    with pytest.raises(SystemExit) as caught:
        module._extractor_worker()
    assert caught.value.code == 1
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_real_child_oversized_stdout_is_killed_and_reaped_without_deadlock(monkeypatch):
    original = asyncio.create_subprocess_exec
    created = []
    async def spawn(*args, **kwargs):
        process = await original(sys.executable, "-I", "-c",
            "import sys,time;sys.stdout.buffer.write(b'x'*1048576);sys.stdout.flush();time.sleep(60)", **kwargs)
        created.append(process)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    async with asyncio.timeout(5):
        with pytest.raises(MusicError) as caught:
            await YTDLPResolver(timeout=4).resolve("title")
    assert caught.value.code == "source_unavailable"
    assert len(created) == 1 and created[0].returncode is not None


@pytest.mark.asyncio
async def test_real_child_cancellation_remains_cancellation_after_reaping(monkeypatch):
    original = asyncio.create_subprocess_exec
    ready = asyncio.Event()
    created = []
    async def spawn(*args, **kwargs):
        process = await original(sys.executable, "-I", "-c", "import time;time.sleep(60)", **kwargs)
        created.append(process)
        ready.set()
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(YTDLPResolver().resolve("title"))
    await ready.wait()
    task.cancel()
    async with asyncio.timeout(5):
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(created) == 1 and created[0].returncode is not None


@pytest.mark.asyncio
async def test_actual_worker_invalid_input_keeps_empty_safe_error_channel():
    process = await asyncio.create_subprocess_exec(sys.executable, "-I", str(Path(module.__file__)), "--resolve",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    async with asyncio.timeout(5):
        output, error = await process.communicate(b'{"query":"file:///synthetic-secret"}')
    assert process.returncode == 1 and output == error == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [
    (MusicError("synthetic-token", code="tls"), "tls"),
    (MusicError("synthetic-token", code="malicious-code"), "source_unavailable"),
    (RuntimeError("synthetic-token"), "playback_error"),
    (TimeoutError("synthetic-token"), "extractor_timeout"),
], ids=["typed", "untrusted-code", "unexpected", "outer-timeout"])
async def test_player_snapshot_and_logs_keep_only_safe_category(error, expected, caplog):
    class Backend:
        def is_connected(self):
            return True
        def stop(self):
            pass
    class Resolver:
        async def resolve(self, source):
            raise error
    manager = MusicManager(MusicOptions(), ["track"], Resolver())
    try:
        player = manager.attach(1, Backend())
        for _ in range(100):
            if player.state == "backoff":
                break
            await asyncio.sleep(.001)
        snapshot = player.snapshot()
        assert snapshot["state"] == "backoff"
        assert snapshot["error_code"] == expected
        assert snapshot["error"] == failure_message(expected)
        assert "synthetic-token" not in caplog.text and "malicious-code" not in caplog.text
    finally:
        await manager.close()
