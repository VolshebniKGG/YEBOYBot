"""Read-only configuration, dependency and SQLite revision checks; no Discord login."""
import importlib.metadata
import os
import sys
import sqlite3
from contextlib import closing
from pathlib import Path

from .config import ROOT, Settings


def sqlite_preflight(database, root=ROOT):
    try:
        from sqlalchemy.engine import make_url
        from .db import CURRENT_SCHEMA_VERSION
        from .migrations import inspect_sqlite
        url = make_url(database)
        if url.drivername != "sqlite+aiosqlite" or not url.database or url.database == ":memory:":
            return None
        path = Path(url.database)
        path = path if path.is_absolute() else root / path
        if not path.exists():
            return None  # Startup bootstraps a fresh schema; doctor never creates files.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            version = inspect_sqlite(connection)
        if version not in (0, CURRENT_SCHEMA_VERSION):
            return "Потрібна міграція SQLite через [21] після зупинки [3]."
    except Exception:
        return "Схему SQLite не підтверджено. Перевірте [4] та журнал; дані не змінено."
    return None


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
        database_failure = sqlite_preflight(settings.database, ROOT)
        if database_failure:
            failures.append(database_failure)
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
