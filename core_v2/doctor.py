"""Read-only configuration/dependency checks; never contacts Discord."""
import importlib.metadata
import os
import sys

from .config import ROOT, Settings


def main():
    os.chdir(ROOT)
    failures = []
    if sys.version_info < (3, 12):
        failures.append("Потрібен Python 3.12 або новіший; CI перевіряє 3.12.")
    for package in ("discord.py", "SQLAlchemy", "aiosqlite", "asyncpg", "python-dotenv"):
        try:
            print(f"{package}: {importlib.metadata.version(package)}")
        except importlib.metadata.PackageNotFoundError:
            failures.append(f"Не встановлено {package}")
    try:
        settings = Settings.load()
        print(f"DB: {'SQLite' if settings.database.startswith('sqlite') else 'PostgreSQL'}")
        print(f"Command sync: {settings.sync}")
        if settings.music:
            failures.append("ENABLE_MUSIC має бути false: модуль ще не інтегровано.")
    except (ValueError, OSError):
        failures.append("Перевірте .env.v2: токен, ID сервера, sync, URL БД і true/false.")
    for failure in failures:
        print(f"ПОМИЛКА: {failure}")
    print("OK (без підключення до Discord)" if not failures else "Виправте помилки вище.")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
