"""Control only our OS-locked runtime; never kill a PID or unrelated Python."""
import json
import subprocess
import sys
import time
from pathlib import Path

from core_v2.runtime import InstanceLock

ROOT = Path(__file__).resolve().parents[2]


def locked(root=ROOT):
    try:
        with InstanceLock(root / "data/v2/bot.lock"):
            return False
    except RuntimeError:
        return True


def state(root=ROOT):
    if not locked(root):
        return {"status": "Зупинено"}
    try:
        data = json.loads((root / "data/v2/managed.json").read_text(encoding="utf-8"))
        age = time.time() - data["heartbeat"]
        data["status"] = ("Готовий" if data["ready"] else "Підключення") if 0 <= age < 10 else "Не відповідає"
        if data.get("recovering") and 0 <= age < 10:
            data["status"] = "Відновлення після помилки"
        return data
    except (OSError, ValueError, KeyError):
        return {"status": "Інший або некерований запуск; зупиніть його у власному вікні"}


def stop(root=ROOT, timeout=30):
    current = state(root)
    if not locked(root):
        return True
    if "instance" not in current:
        raise RuntimeError("Не можна підтвердити керований процес")
    path = root / "data/v2/stop.json"
    temporary = path.with_suffix(".request")
    temporary.write_text(json.dumps({"instance": current["instance"]}), encoding="utf-8")
    temporary.replace(path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not locked(root):
            return True
        time.sleep(.2)
    raise RuntimeError("Процес не відповідає; примусове завершення не виконано")


def start(root=ROOT, timeout=30):
    if locked(root):
        return state(root)
    with InstanceLock(root / "data/v2/launch.lock"):
        if locked(root):
            return state(root)
        child = subprocess.Popen([sys.executable, "-m", "core_v2.managed"], cwd=root,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("Бот завершив запуск; перевірте конфігурацію та журнал")
            current = state(root)
            if current["status"] == "Готовий":
                return current
            time.sleep(.2)
        if not locked(root):
            raise RuntimeError("Запуск не підтверджено")
        return state(root)


def restart(root=ROOT):
    stop(root)
    return start(root)
