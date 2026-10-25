"""Conservative fast-forward updater. Never resets, stashes or auto-downgrades."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from core_v2.runtime import InstanceLock
from . import process, storage

ROOT = Path(__file__).resolve().parents[2]
BRANCH = "feature/core-v2"
REMOTE = "https://github.com/VolshebniKGG/YEBOYBot.git"


class UpdateError(RuntimeError):
    pass


def git(root, *args):
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(["git", *args], cwd=root, env=environment, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError("Git недоступний або час операції вичерпано.") from exc
    if result.returncode:
        # Git stderr can include credential-bearing remote URLs.
        raise UpdateError("Git не виконав операцію. Перевірте мережу, гілку та стан репозиторію.")
    return result.stdout.strip()


def clean(root):
    if git(root, "branch", "--show-current") != BRANCH:
        raise UpdateError("Оновлення дозволено лише в feature/core-v2; автоматичного перемикання немає.")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise UpdateError("Є локальні зміни або нові файли. Збережіть їх перед оновленням.")
    if git(root, "remote", "get-url", "origin").rstrip("/") != REMOTE:
        raise UpdateError("origin має вказувати на офіційний HTTPS-репозиторій YEBOYBot.")


def plan(root=ROOT, *, fetch=True):
    clean(root)
    old = git(root, "rev-parse", "HEAD")
    if fetch:
        git(root, "fetch", "--no-tags", REMOTE, f"refs/heads/{BRANCH}")
        target = git(root, "rev-parse", "FETCH_HEAD")
    else:
        target = git(root, "rev-parse", f"refs/remotes/origin/{BRANCH}")
    if git(root, "merge-base", old, target) != old:
        raise UpdateError("Історії розійшлися. Потрібне ручне узгодження без втрати локальних комітів.")
    changed = git(root, "diff", "--name-only", old, target).splitlines()
    for path in changed:
        path = path.lower()
        if path.startswith(("data/", "backups/", "logs/", ".venv", ".git/")) or path in {".env", ".env.v2"}:
            raise UpdateError("Оновлення змінює локальні дані або секрети; автоматичне застосування заблоковане.")
    # An ignored file may otherwise be overwritten by Git when newly tracked.
    added = git(root, "diff", "--diff-filter=A", "--name-only", old, target).splitlines()
    if any((root / name).exists() for name in added):
        raise UpdateError("Новий файл оновлення вже існує локально. Оновлення заблоковано.")
    return {"old": old, "target": target, "files": changed,
            "summary": git(root, "log", "--format=%h %s", "--max-count=10", f"{old}..{target}")}


def python(root, *args):
    try:
        result = subprocess.run([sys.executable, *args], cwd=root, capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError("Python-операція не завершилася; перевірте стан перед запуском.") from exc
    if result.returncode:
        raise UpdateError("Перевірка Python або залежностей не пройдена. Бот залишено зупиненим.")


def apply(update, root=ROOT):
    report = {"old": update["old"], "target": update["target"], "phase": "preflight",
              "time": time.time()}
    report_path = root / "data/v2/update-report.json"
    was_running = False
    with InstanceLock(root / "data/v2/launch.lock"):
        clean(root)
        if git(root, "rev-parse", "HEAD") != update["old"]:
            raise UpdateError("HEAD змінився після перегляду. Повторіть перевірку оновлення.")
        if update["target"] == update["old"]:
            return False
        # Validate backend before stopping the bot. PostgreSQL requires its own backup workflow.
        source = storage.database_path(root)
        was_running = process.locked(root)
        if was_running:
            process.stop(root)
        with InstanceLock(root / "data/v2/bot.lock"):
            try:
                if source.exists():
                    report["backup"] = str(storage.create(root))
                report["phase"] = "backup_complete"
                report_path.write_text(json.dumps(report), encoding="utf-8")
                git(root, "merge", "--ff-only", update["target"])
                report["phase"] = "code_updated"
                report_path.write_text(json.dumps(report), encoding="utf-8")
                if "core_v2/requirements.txt" in update["files"]:
                    python(root, "-m", "pip", "install", "-r", "core_v2/requirements.txt")
                python(root, "-m", "pip", "check")
                # Fresh interpreter imports new code; no Discord login.
                python(root, "-m", "tools.launcher.verify_update")
                report["phase"] = "verified"
            except Exception as exc:
                report["error_type"] = type(exc).__name__
                raise
            finally:
                report_path.write_text(json.dumps(report), encoding="utf-8")
    if was_running:
        # Fresh interpreter avoids using pre-update modules in the menu process.
        python(root, "-c", "from tools.launcher.process import start; print(start()['status'])")
    return True


def menu(root=ROOT):
    if sys.prefix == sys.base_prefix:
        raise UpdateError("Оновлення запускайте з .venv-v2 через YEBOYBot.cmd.")
    update = plan(root)
    if update["old"] == update["target"]:
        print("Оновлень немає.")
        return False
    print(update["summary"])
    print("Бот буде зупинено; перед оновленням створиться SQLite backup.")
    if input("Застосувати оновлення? Введіть ОНОВИТИ: ").strip() != "ОНОВИТИ":
        return False
    apply(update, root)
    print("Оновлення перевірено. Відкрийте YEBOYBot.cmd знову для нового меню.")
    return True
