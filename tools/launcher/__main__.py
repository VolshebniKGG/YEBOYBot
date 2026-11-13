"""Windows console control center for the existing Core V2 runtime."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
SETUP_EXIT_CODE = 24
REPAIR_EXIT_CODE = 25
UPDATE_EXIT_CODE = 26


def run(*args):
    return subprocess.call([PY, *args], cwd=ROOT)


def setup():
    from .configuration import wizard
    wizard(ROOT)


def full_setup():
    """Unload native Python packages before the parent PowerShell installer."""
    if sys.platform != "win32":
        print("Повне встановлення через цей пункт доступне лише у Windows.")
        return 1
    print("Меню завершується; встановлення продовжиться в цьому ж вікні.")
    return SETUP_EXIT_CODE


def repair_dependencies():
    if sys.platform == "win32":
        print("Меню завершується; бібліотеки оновляться в цьому ж вікні.")
        return REPAIR_EXIT_CODE
    return run("-m", "tools.launcher.bootstrap", "--repair")


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


def installed_version_label(root=ROOT):
    """Small offline branch/commit label; status remains available on Git errors."""
    try:
        from .github_update import installed_version
        sha, branch = installed_version(root)
    except (OSError, RuntimeError, ValueError, TypeError):
        return "не визначено"
    name = branch if isinstance(branch, str) else "невідома"
    if sha:
        return f"{name} · {sha[:10]}"
    return name


def main():
    os.chdir(ROOT)
    from . import process, storage, update, configuration, autostart, migrations, pg_workflow
    from . import media, rank_import
    from .interface import show_menu

    # Keep every existing capability, but present exactly 1..21 without gaps.
    actions = {
        "1": ("Запустити Discord-бота", start),
        "2": ("Перезапустити бота", lambda: print(process.restart()["status"])),
        "3": ("Зупинити бота", lambda: print("Зупинено" if process.stop() else "Не зупинено")),
        "4": ("Перевірити конфігурацію та БД", lambda: run("-m", "tools.launcher.diagnostics")),
        "5": ("Оновити Python-залежності", repair_dependencies),
        "6": ("Оновити YEBOYBot із GitHub (вибір гілки)", update.menu),
        "7": ("Створити резервну копію SQLite", backup),
        "8": ("Відновити SQLite з резервної копії", storage.restore_menu),
        "9": ("Переглянути журнал", logs),
        "10": ("Перевірити SQLite", storage.check),
        "11": ("Налаштувати Discord (приховане введення токена)", setup),
        "12": ("Початкове налаштування", lambda: (setup(), run("-m", "core_v2.doctor"))),
        "13": ("Автозапуск Windows", autostart.menu),
        "14": ("Експортувати звіт діагностики", lambda: run("-m", "tools.launcher.diagnostics", "--export")),
        "15": ("Резервні копії: перевірка й політика зберігання", storage.catalog_menu),
        "16": ("План і застосування міграцій SQLite", migrations.menu),
        "17": ("Старі функції (окреме legacy-середовище)", lambda: run("-I", str(ROOT / "tools/legacy_launcher.py"))),
        "18": ("PostgreSQL: резервування, міграція, recovery", pg_workflow.menu),
        "19": ("Повне встановлення / відновлення Core V2", full_setup),
        "20": ("Музика: автоматична підготовка й увімкнення", media.menu),
        "21": ("Перенести старий XP у Core V2 (SQLite)", rank_import.menu),
    }

    while True:
        show_menu(actions, process.state()["status"], installed_version_label(ROOT))
        try:
            choice = input("  Номер дії › ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nЗавершення меню без зупинки бота.")
            return 0
        if choice == "0":
            return 0
        if choice not in actions:
            print("Номер не знайдено. Оберіть 1–21 або [0], щоб закрити панель.")
            continue
        try:
            print(f"\n  › {actions[choice][0]}\n")
            result = actions[choice][1]()
            if choice == "19" and result == SETUP_EXIT_CODE:
                return SETUP_EXIT_CODE
            if choice == "5" and result == REPAIR_EXIT_CODE and sys.platform == "win32":
                return REPAIR_EXIT_CODE
            if choice == "6" and result == UPDATE_EXIT_CODE and sys.platform == "win32":
                return UPDATE_EXIT_CODE
            if choice == "6" and result == UPDATE_EXIT_CODE:
                print("Автоматичне оновлення через зовнішній worker працює у Windows.")
                return 1
        except (storage.StorageError, update.UpdateError, configuration.ConfigurationError,
                migrations.MigrationError, rank_import.RankImportError) as exc:
            print(str(exc))
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(f"Помилка операції: {type(exc).__name__}")
            print("Перевірте конфігурацію та доступність файлів.")


if __name__ == "__main__":
    raise SystemExit(main())
