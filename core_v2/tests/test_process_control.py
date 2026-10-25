import json
import threading
import time

import pytest

from core_v2.runtime import InstanceLock
from tools.launcher.process import state, stop


def test_stale_pid_never_counts_as_running(tmp_path):
    folder = tmp_path / "data/v2"
    folder.mkdir(parents=True)
    (folder / "managed.json").write_text(json.dumps({"pid": 1, "heartbeat": time.time(), "ready": True}))
    assert state(tmp_path)["status"] == "Зупинено"
    assert stop(tmp_path)


def test_unmanaged_process_never_killed(tmp_path):
    with InstanceLock(tmp_path / "data/v2/bot.lock"):
        with pytest.raises(RuntimeError):
            stop(tmp_path, timeout=.1)


def test_cooperative_stop_nonce(tmp_path):
    folder = tmp_path / "data/v2"
    ready = threading.Event()
    def worker():
        with InstanceLock(folder / "bot.lock"):
            (folder / "managed.json").write_text(json.dumps(
                {"instance": "unique", "heartbeat": time.time(), "ready": True}))
            ready.set()
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                try:
                    if json.loads((folder / "stop.json").read_text())["instance"] == "unique":
                        return
                except FileNotFoundError:
                    pass
                time.sleep(.02)
    thread = threading.Thread(target=worker)
    thread.start()
    assert ready.wait(3)
    assert state(tmp_path)["status"] == "Готовий"
    assert stop(tmp_path, timeout=3)
    thread.join(3)
    assert not thread.is_alive()


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows CMD only")
def test_cmd_unicode_path(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    source = Path(__file__).resolve().parents[2]
    root = tmp_path / "Бот з пробілами"
    root.mkdir()
    for name in ("core_v2", "tools"):
        shutil.copytree(source / name, root / name)
    shutil.copy(source / "YEBOYBot.cmd", root)
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages",
                    str(root / ".venv-v2")], check=True, timeout=60)
    result = subprocess.run(["cmd.exe", "/d", "/c", str(root / "YEBOYBot.cmd")],
                            input="0\n", text=True, capture_output=True, timeout=30, cwd=tmp_path)
    assert result.returncode == 0
