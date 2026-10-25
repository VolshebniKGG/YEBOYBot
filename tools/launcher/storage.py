"""Local SQLite maintenance; no tokens are copied into backups."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from core_v2.backup import backup
from core_v2.db import meta
from core_v2.runtime import InstanceLock

ROOT = Path(__file__).resolve().parents[2]


class StorageError(RuntimeError):
    pass


def database_path(root=ROOT):
    import os
    config = dotenv_values(root / ".env.v2")
    url = make_url(os.environ.get("DATABASE_URL") or config.get("DATABASE_URL") or
                   "sqlite+aiosqlite:///data/v2/yeboy.db")
    if url.drivername != "sqlite+aiosqlite" or not url.database or url.database == ":memory:" or url.query:
        raise StorageError("Це меню підтримує лише файлову SQLite без URL-параметрів. Для PostgreSQL використовуйте pg_dump.")
    path = Path(url.database)
    return (path if path.is_absolute() else root / path).resolve()


def validate(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise StorageError("Файл бази даних не знайдено.")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise StorageError("Перевірка цілісності не пройдена.")
        versions = conn.execute("SELECT version FROM v2_schema").fetchall()
        if versions != [(1,)]:
            raise StorageError("Схема несумісна з поточним ядром (очікується v1).")
        for table in meta.sorted_tables:
            columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table.name}")')}
            if not {c.name for c in table.columns} <= columns:
                raise StorageError("Відсутні потрібні таблиці або колонки.")
    return True


def create(root=ROOT):
    source = database_path(root)
    destination = root / "backups" / ("v2-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".db")
    backup(source, destination)
    try:
        validate(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def available(root=ROOT):
    return sorted((root / "backups").glob("v2-*.db"), reverse=True)


def restore(source, *, root=ROOT, confirmed=False):
    if not confirmed:
        raise StorageError("Відновлення потребує явного підтвердження.")
    source = Path(source).resolve()
    if source not in [p.resolve() for p in available(root)]:
        raise StorageError("Виберіть копію зі списку цього проєкту.")
    # Same lock as both managed and direct core entry points, held throughout.
    with InstanceLock(root / "data/v2/bot.lock"):
        target = database_path(root)
        if target == source:
            raise StorageError("Джерело й поточна база збігаються.")
        validate(source)
        previous = create(root) if target.exists() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(target)) as destination:
                original.backup(destination)
        validate(target)
        return previous


def restore_menu(root=ROOT):
    copies = available(root)
    if not copies:
        print("Резервних копій немає.")
        return
    for i, path in enumerate(copies, 1):
        print(f"[{i}] {path.name}")
    value = input("Номер копії (Enter — скасувати): ").strip()
    if not value:
        return
    if not value.isdigit() or not 1 <= int(value) <= len(copies):
        raise StorageError("Неправильний номер.")
    chosen = copies[int(value)-1]
    validate(chosen)
    print("Зупиніть бот через [3]. Поточні дані буде замінено; спочатку створиться їх резервна копія.")
    if input("Для підтвердження введіть ВІДНОВИТИ: ").strip() != "ВІДНОВИТИ":
        print("Скасовано.")
        return
    previous = restore(chosen, root=root, confirmed=True)
    print("Відновлення завершено. Попередня копія:", previous or "база була відсутня")


def check(root=ROOT):
    validate(database_path(root))
    print("SQLite: цілісність і схема v1 перевірені. Дані не змінено.")
