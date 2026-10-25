"""Explicit offline SQLite migrations with a verified pre-change recovery copy."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from core_v2 import migrations as engine
from core_v2.runtime import InstanceLock
from . import storage

ROOT = Path(__file__).resolve().parents[2]


class MigrationError(RuntimeError):
    pass


def _identity(path):
    info = path.stat()
    return hashlib.sha256((str(path.resolve()) + f"\0{info.st_dev}\0{info.st_ino}").encode("utf-8")).hexdigest()


def _prepare(root):
    try:
        path = storage.database_path(root)
        result = engine.plan_sqlite(path)
        return path, dict(result, source_id=_identity(path))
    except storage.StorageError as error:
        raise MigrationError("Міграції меню підтримують файлову SQLite. PostgreSQL v1 потребує окремого перевіреного pg_dump workflow.") from error
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        raise MigrationError("План не отримано: перевірте наявність, цілісність і підтримувану схему БД. Нова БД створюється під час запуску [1].") from error


def plan(root=ROOT):
    return _prepare(root)[1]


def write_report(root, report):
    folder = Path(root) / "data/v2"
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".migration-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(report, target, ensure_ascii=False, indent=2)
            target.write("\n")
        os.replace(name, folder / "migration-report.json")
    finally:
        Path(name).unlink(missing_ok=True)


def apply(root=ROOT, *, confirmed=False, expected_plan=None):
    if not confirmed:
        raise MigrationError("Міграція потребує явного підтвердження.")
    # Same ordering as restore: exclude bot/start/update and other maintenance.
    with InstanceLock(Path(root) / "data/v2/bot.lock"), storage.maintenance_lock(root):
        path, current_plan = _prepare(root)
        if expected_plan is not None and current_plan != expected_plan:
            raise MigrationError("План або файл БД змінився. Повторіть перегляд і підтвердження.")
        report = {"from": current_plan["current"], "to": current_plan["target"],
                  "revisions": current_plan["revisions"], "status": "unchanged", "backup": None}
        if not current_plan["revisions"]:
            return report
        committed = False
        with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5, isolation_level=None)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                # Guard again after obtaining SQLite's writer lock, before any DDL.
                if (engine.inspect_sqlite(connection) != current_plan["current"] or
                        _identity(path) != current_plan["source_id"] or storage.database_path(root) != path):
                    raise MigrationError("Версія, файл або конфігурація БД змінилися. Повторіть перегляд.")
                saved = storage._create(root, source=path)
                info = storage._require_verified(saved)
                if info["schema_version"] != current_plan["current"]:
                    raise MigrationError("Версія резервної копії не відповідає джерелу.")
                report["backup"] = str(saved.relative_to(Path(root)))
                report["status"] = "backed_up"
                write_report(root, report)
                if storage.database_path(root) != path or _identity(path) != current_plan["source_id"]:
                    raise MigrationError("Файл або конфігурація БД змінилися під час підготовки.")
                report["revisions"] = engine.migrate_sqlite(connection, current_plan["target"])
                if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise MigrationError("Перевірка після міграції не пройдена.")
                if engine.inspect_sqlite(connection) != current_plan["target"]:
                    raise MigrationError("Версія після міграції не підтверджена.")
                connection.commit()
                committed = True
                report["status"] = "applied"
                write_report(root, report)
            except Exception as error:
                connection.rollback()
                if committed:
                    raise MigrationError("БД оновлено, але звіт не записано. Перевірте [4]/[21] перед подальшими діями.") from error
                report["status"] = "failed"
                report["error_type"] = type(error).__name__
                try:
                    write_report(root, report)
                except OSError:
                    pass
                raise MigrationError("Міграцію не підтверджено. Залиште бот зупиненим і перевірте схему [4]/[21], звіт та резервну копію.") from error
        return report


def menu(root=ROOT):
    preview = plan(root)
    print(f"Схема v{preview['current']} → v{preview['target']}")
    if not preview["revisions"]:
        print("Схема актуальна. Змін немає.")
        return
    print("Ревізії:", ", ".join(preview["revisions"]))
    print("Бот має бути зупинений через [3]. Спочатку буде створено перевірену резервну копію.")
    print("v2 додає час останньої зміни модераційної справи; наявні записи зберігаються.")
    if input("Для підтвердження введіть МІГРУВАТИ: ").strip() != "МІГРУВАТИ":
        print("Скасовано.")
        return
    report = apply(root, confirmed=True, expected_plan=preview)
    print("Міграцію завершено. Резервна копія:", report["backup"])
    print("Перевірте [4], потім запустіть бот через [1].")
