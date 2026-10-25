"""Windows console menu for the existing Core V2 runtime."""
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(*args):
    return subprocess.call([PY, *args], cwd=ROOT)


def setup():
    env = ROOT / ".env.v2"
    if not env.exists():
        env.write_bytes((ROOT / ".env.v2.example").read_bytes())
        print("Створено .env.v2. Відредагуйте файл перед запуском.")
    else:
        print(".env.v2 вже існує; дані збережено.")


def start():
    if run("-m", "core_v2.doctor"):
        print("Перевірка конфігурації не пройдена.")
        return
    print("Запуск Core V2. Для зупинення натисніть Ctrl+C.")
    try:
        run("-m", "core_v2")
    except KeyboardInterrupt:
        print("Зупинення запрошено.")


def backup():
    dest = ROOT / "backups" / ("v2-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".db")
    if run("-m", "core_v2.backup", str(dest)) == 0:
        print("Резервна копія:", dest)


def logs():
    path = ROOT / "data/v2/logs/bot.log"
    if path.exists():
        print("\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]))
    else:
        print("Журнал поки не створено.")


def main():
    os.chdir(ROOT)
    actions = {
        "1": ("Запустити Discord-бота (Ctrl+C для зупинення)", start),
        "4": ("Перевірити систему", lambda: run("-m", "core_v2.doctor")),
        "5": ("Оновити Python-залежності", lambda: run("-m", "pip", "install", "-r", "core_v2/requirements.txt")),
        "7": ("Створити резервну копію SQLite", backup),
        "9": ("Переглянути журнал", logs),
        "11": ("Створити конфігурацію .env.v2", setup),
        "12": ("Початкове налаштування", lambda: (setup(), run("-m", "core_v2.doctor"))),
    }
    while True:
        print("\n=== YEBOYBot Core V2 | Windows ===")
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
            actions[choice][1]()
        except (OSError, ValueError) as exc:
            print(f"Помилка операції: {type(exc).__name__}")
            print("Перевірте конфігурацію та доступність файлів.")


if __name__ == "__main__":
    raise SystemExit(main())
