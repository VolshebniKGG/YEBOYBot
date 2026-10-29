"""Managed lifecycle with real local state I/O and no Discord network."""
import asyncio
from types import SimpleNamespace

import pytest

from core_v2 import managed


class LocalBot:
    def __init__(self, settings):
        from unittest.mock import AsyncMock
        self.db = SimpleNamespace(initialize=AsyncMock())
        self.closed = asyncio.Event()
        self.active = False
        self.start_finished = False
        self.was_closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def start(self, token):
        self.active = True
        try:
            await self.closed.wait()
        finally:
            self.active = False
            self.start_finished = True

    async def close(self):
        self.was_closed = True
        self.closed.set()

    def is_ready(self):
        return self.active


@pytest.fixture
def local_runtime(tmp_path, monkeypatch):
    from core_v2 import bot
    instances = []
    def factory(settings):
        instance = LocalBot(settings)
        instances.append(instance)
        return instance
    monkeypatch.setattr(bot, "Bot", factory)
    monkeypatch.setattr(managed, "STATE", tmp_path / "managed.json")
    monkeypatch.setattr(managed, "STOP", tmp_path / "stop.json")
    return SimpleNamespace(settings=SimpleNamespace(token="synthetic-token"), instances=instances,
                           root=tmp_path)


async def test_failed_heartbeat_does_not_leave_client_running(local_runtime):
    # A directory at the destination reproduces an actual atomic-replace I/O error.
    managed.STATE.mkdir()
    with pytest.raises(RuntimeError, match="heartbeat"):
        await asyncio.wait_for(managed.run(local_runtime.settings, "owned-instance"), timeout=1)
    instance = local_runtime.instances[0]
    assert instance.was_closed and instance.start_finished and not instance.active
    assert managed.STATE.is_dir()  # Do not remove an unexpected user-created object.


async def test_heartbeat_failure_is_fatal_without_retry(local_runtime):
    managed.STATE.mkdir()
    waits = []
    async def sleep(seconds):
        waits.append(seconds)
    with pytest.raises(managed.ControlFailure):
        await asyncio.wait_for(managed.recover(
            lambda: managed.run(local_runtime.settings, "instance"), lambda: False,
            lambda *_: None, (1, 1), sleep), timeout=3)
    assert len(local_runtime.instances) == 1
    assert waits == []
    assert local_runtime.instances[0].was_closed


async def test_requested_stop_takes_priority_over_failed_heartbeat(local_runtime):
    import json
    managed.STOP.write_text(json.dumps({"instance": "owned-instance"}))
    managed.STATE.mkdir()
    await asyncio.wait_for(managed.run(local_runtime.settings, "owned-instance"), timeout=3)
    assert local_runtime.instances[0].was_closed
    assert local_runtime.instances[0].start_finished
    assert managed.STATE.is_dir()
    # No replacement attempted once the authenticated stop request is accepted.
    assert not managed.STATE.with_suffix(".tmp").exists()
    # Other instance requests do not stop this one.
    managed.STOP.write_text(json.dumps({"instance": "other-instance"}))
    assert not managed.stop_requested("owned-instance")


async def test_client_auth_failure_keeps_original_type_and_is_not_retried(local_runtime, monkeypatch):
    from discord import LoginFailure
    async def failed_start(self, token):
        self.start_finished = True
        raise LoginFailure("synthetic-auth-error")
    monkeypatch.setattr(LocalBot, "start", failed_start)
    with pytest.raises(LoginFailure):
        await asyncio.wait_for(managed.recover(
            lambda: managed.run(local_runtime.settings, "instance"), lambda: False,
            lambda *_: None, (0,)), timeout=3)
    assert len(local_runtime.instances) == 1
    assert local_runtime.instances[0].was_closed


async def test_network_retry_closes_client_before_new_attempt(local_runtime, monkeypatch):
    calls = []
    async def retrying_start(self, token):
        if calls:
            assert calls[-1].was_closed and not calls[-1].active
        calls.append(self)
        self.active = True
        try:
            if len(calls) == 1:
                raise OSError("synthetic-offline")
        finally:
            self.active = False
            self.start_finished = True
    monkeypatch.setattr(LocalBot, "start", retrying_start)
    await asyncio.wait_for(managed.recover(
        lambda: managed.run(local_runtime.settings, "instance"), lambda: False,
        lambda *_: None, (0,)), timeout=3)
    assert len(calls) == 2
    assert all(bot.was_closed for bot in calls)


async def test_outer_cancellation_joins_both_tasks(local_runtime):
    operation = asyncio.create_task(managed.run(local_runtime.settings, "instance"))
    for _ in range(10):
        await asyncio.sleep(0)
        if local_runtime.instances and local_runtime.instances[0].active:
            break
    assert local_runtime.instances[0].active
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert local_runtime.instances[0].was_closed
    assert not local_runtime.instances[0].active
    assert not [task for task in asyncio.all_tasks() if task.get_name() in
                {"discord-client", "managed-control"} and not task.done()]


def test_failed_atomic_replace_preserves_last_heartbeat(tmp_path, monkeypatch):
    import json
    path = tmp_path / "managed.json"
    managed.write_state(path, {"ready": False})
    def deny(*args):
        raise PermissionError("synthetic-publish-error")
    monkeypatch.setattr(managed.os, "replace", deny)
    with pytest.raises(managed.ControlFailure):
        managed.write_state(path, {"ready": True})
    assert json.loads(path.read_text()) == {"ready": False}
    assert not path.with_suffix(".tmp").exists()


def test_process_exits_on_control_failure_and_releases_lock(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    from core_v2.runtime import InstanceLock
    root = tmp_path / "Бот з пробілами"
    root.mkdir()
    script = '''
import runpy, sys
from pathlib import Path
from types import SimpleNamespace
from core_v2 import managed, bot
namespace = runpy.run_path(sys.argv[2])
root = Path(sys.argv[1])
class RecordingBot(namespace['LocalBot']):
    async def __aexit__(self, *args):
        await super().__aexit__(*args)
        (root / 'closed.txt').write_text('closed')
bot.Bot = RecordingBot
managed.ROOT = root
managed.STATE = root / 'data/v2/managed.json'
managed.STOP = root / 'data/v2/stop.json'
managed.STATE.mkdir(parents=True)
managed.Settings.load = lambda: SimpleNamespace(token='synthetic-token', database='synthetic-db')
raise SystemExit(managed.main())
'''
    result = subprocess.run([sys.executable, "-c", script, str(root), str(Path(__file__).resolve())],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=15)
    assert result.returncode == 1, result.stderr
    assert (root / "closed.txt").read_text() == "closed"
    assert (root / "data/v2/managed.json").is_dir()
    assert "synthetic-token" not in result.stdout + result.stderr
    with InstanceLock(root / "data/v2/bot.lock"):
        pass


async def test_lost_heartbeat_after_ready_closes_established_session(local_runtime):
    import json
    operation = asyncio.create_task(managed.run(local_runtime.settings, "instance"))
    try:
        async with asyncio.timeout(3):
            while not managed.STATE.is_file():
                await asyncio.sleep(.005)
        assert json.loads(managed.STATE.read_text())["ready"] is True
        assert local_runtime.instances[0].active
        managed.STATE.unlink()
        managed.STATE.mkdir()
        with pytest.raises(managed.ControlFailure):
            await asyncio.wait_for(operation, timeout=3)
        assert local_runtime.instances[0].was_closed
        assert not local_runtime.instances[0].active
        assert not [task for task in asyncio.all_tasks() if task.get_name() in
                    {"discord-client", "managed-control"} and not task.done()]
    finally:
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


async def test_migration_required_prevents_discord_login_and_retry(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from core_v2.bot import Bot
    from core_v2.config import Settings
    from core_v2.db import MigrationRequired
    from test_migrations_core import create_legacy_v1
    path = create_legacy_v1(tmp_path / "legacy.db")
    monkeypatch.setattr(managed, "STATE", tmp_path / "managed.json")
    monkeypatch.setattr(managed, "STOP", tmp_path / "stop.json")
    start = AsyncMock()
    monkeypatch.setattr(Bot, "start", start)
    with pytest.raises(MigrationRequired):
        await asyncio.wait_for(managed.recover(
            lambda: managed.run(Settings("synthetic-token", "sqlite+aiosqlite:///" + str(path)), "instance"),
            lambda: False, lambda *_: None, (0, 0)), timeout=3)
    start.assert_not_awaited()
