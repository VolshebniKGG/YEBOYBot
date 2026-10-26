"""Windows console menu for the existing Core V2 runtime."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(*args):
    return subprocess.call([PY, *args], cwd=ROOT)


def setup():
    from .configuration import wizard
    wizard(ROOT)


def start():
    if run("-m", "core_v2.doctor"):
        print("Перевірка конфігурації не пройдена.")
        return
    from . import process
    print(process.start()["status"])


def backup():
    from .storage import create
    print("Резервна копія:", create())


def logs():
    path = ROOT / "data/v2/logs/bot.log"
    if path.exists():
        print("\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]))
    else:
        print("Журнал поки не створено.")


def main():
    os.chdir(ROOT)
    from . import process, storage, update, configuration, autostart, migrations, pg_workflow
    actions = {
        "2": ("Перезапустити бота", lambda: print(process.restart()["status"])),
        "3": ("Зупинити бота", lambda: print("Зупинено" if process.stop() else "Не зупинено")),
        "1": ("Запустити Discord-бота", start),
        "4": ("Перевірити конфігурацію та БД", lambda: run("-m", "tools.launcher.diagnostics")),
        "5": ("Оновити Python-залежності", lambda: run("-m", "tools.launcher.bootstrap", "--repair")),
        "6": ("Оновити YEBOYBot із GitHub", update.menu),
        "7": ("Створити резервну копію SQLite", backup),
        "8": ("Відновити SQLite з резервної копії", storage.restore_menu),
        "10": ("Перевірити SQLite", storage.check),
        "9": ("Переглянути журнал", logs),
        "11": ("Налаштувати Discord (приховане введення токена)", setup),
        "15": ("Автозапуск Windows", autostart.menu),
        "21": ("План і застосування міграцій SQLite", migrations.menu),
        "22": ("Старі функції (окреме legacy-середовище)", lambda: run("-I", str(ROOT / "tools/legacy_launcher.py"))),
        "23": ("PostgreSQL: резервування, міграція, recovery", pg_workflow.menu),
        "20": ("Резервні копії: перевірка й політика зберігання", storage.catalog_menu),
        "18": ("Експортувати звіт діагностики", lambda: run("-m", "tools.launcher.diagnostics", "--export")),
        "12": ("Початкове налаштування", lambda: (setup(), run("-m", "core_v2.doctor"))),
    }
    while True:
        print("\n=== YEBOYBot Core V2 | Windows ===")
        print("Стан:", process.state()["status"])
        for key, (label, _) in actions.items():
            print(f"[{key}] {label}")
        print("[0] Вихід")
        choice = input("Виберіть дію: ").strip()
        if choice == "0":
            return 0
        if choice not in actions:
            print("Невідома дія. Недоступні функції не показуються.")
            continue
        try:
            result = actions[choice][1]()
            if choice == "6" and result:
                return 0
        except (storage.StorageError, update.UpdateError, configuration.ConfigurationError, migrations.MigrationError) as exc:
            print(str(exc))
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(f"Помилка операції: {type(exc).__name__}")
            print("Перевірте конфігурацію та доступність файлів.")


if __name__ == "__main__":
    raise SystemExit(main())

