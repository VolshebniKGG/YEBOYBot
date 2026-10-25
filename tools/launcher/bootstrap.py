"""Repeatable Windows installation using only the standard library initially."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

from core_v2.runtime import InstanceLock

ROOT = Path(__file__).resolve().parents[2]
IMPORT_CHECK = "import discord, sqlalchemy, aiosqlite, asyncpg, dotenv"


class BootstrapError(RuntimeError):
    pass


def run(args, root):
    try:
        # Do not capture or reprint environment variables or configuration.
        result = subprocess.run(args, cwd=root, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise BootstrapError("Час встановлення вичерпано. Перевірте мережу та повторіть запуск.") from exc
    if result.returncode:
        raise BootstrapError("Перевірка або встановлення не вдалося. Перевірте повідомлення вище й повторіть запуск.")


def python_path(root):
    return root / ".venv-v2" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def prepare(root=ROOT, *, repair=False):
    if sys.version_info[:2] != (3, 12):
        raise BootstrapError("Потрібен Python 3.12: https://www.python.org/downloads/ . Інші версії ще не перевірені.")
    root = Path(root).resolve()
    # Serialize installation and exclude a running bot while pip may change libraries.
    with InstanceLock(root / "data/v2/install.lock"), InstanceLock(root / "data/v2/bot.lock"):
        interpreter = python_path(root)
        requirements = root / "core_v2/requirements.txt"
        stamp = root / ".venv-v2/yeboy-requirements.sha256"
        digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
        if not interpreter.is_file():
            print("Створення .venv-v2…")
            venv.EnvBuilder(with_pip=True).create(root / ".venv-v2")
        run([str(interpreter), "-c", "import sys; sys.exit(sys.version_info[:2] != (3, 12))"], root)
        install = repair or not stamp.is_file() or stamp.read_text(encoding="ascii").strip() != digest
        if install:
            # A failed installation must never retain a successful marker.
            stamp.unlink(missing_ok=True)
            run([str(interpreter), "-m", "pip", "install", "-r", str(requirements)], root)
        try:
            run([str(interpreter), "-m", "pip", "check"], root)
            run([str(interpreter), "-c", IMPORT_CHECK], root)
        except BootstrapError:
            stamp.unlink(missing_ok=True)
            raise
        stamp.write_text(digest + "\n", encoding="ascii")
        for folder in ("data/v2/logs", "backups"):
            (root / folder).mkdir(parents=True, exist_ok=True)
        # Exclusive creation preserves an existing token and custom settings.
        template = (root / ".env.v2.example").read_text(encoding="utf-8")
        try:
            with (root / ".env.v2").open("x", encoding="utf-8") as target:
                target.write(template)
        except FileExistsError:
            pass
    print("Середовище готове. Токен і готовність Discord перевіряються окремо через меню.")
    print("Git: " + ("доступний" if shutil.which("git") else "відсутній; Git-оновлення недоступне. https://git-scm.com/downloads/win"))
    return interpreter


def main():
    try:
        prepare(repair="--repair" in sys.argv[1:])
    except (BootstrapError, OSError, RuntimeError) as exc:
        print(f"Встановлення не завершено: {type(exc).__name__}")
        if isinstance(exc, (BootstrapError, RuntimeError)):
            print(str(exc))
        else:
            print("Перевірте права запису, вільне місце та доступність Python. Дані й конфігурація не видаляються.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
