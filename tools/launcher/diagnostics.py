"""Bounded, read-only local checks; export only an explicit allowlist."""
from datetime import datetime, timezone
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ("discord.py", "SQLAlchemy", "aiosqlite", "asyncpg", "python-dotenv")


def result(status, code):
    return {"status": status, "code": code}


def sqlite_check(path):
    """No database creation, migrations, WAL mode changes, or writes."""
    if not path.is_file():
        return result("error", "database_missing")
    deadline = time.monotonic() + 2
    try:
        from core_v2.db import meta
        from contextlib import closing
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            try:
                db.execute("PRAGMA query_only=ON")
                if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    return result("error", "integrity_failed")
                if db.execute("SELECT version FROM v2_schema").fetchall() != [(1,)]:
                    return result("error", "schema_unsupported")
                for table in meta.sorted_tables:
                    columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table.name}")')}
                    if not {column.name for column in table.columns} <= columns:
                        return result("error", "schema_incomplete")
            finally:
                db.set_progress_handler(None, 0)
        return result("ok", "sqlite_schema_v1")
    except Exception:
        # Exception text can include paths, credentials, SQL or user data.
        return result("error", "database_check_failed")


def collect(root=ROOT):
    from . import process
    root = Path(root).resolve()
    checks = {"python": result("ok" if sys.version_info[:2] == (3, 12) else "error", "python_3_12_required")}
    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    imports_ok = True
    for module in ("discord", "sqlalchemy", "aiosqlite", "asyncpg", "dotenv"):
        try:
            importlib.import_module(module)
        except Exception:
            imports_ok = False
    checks["dependencies"] = result("ok" if all(packages.values()) and imports_ok else "error", "packages_and_imports")
    backend = "unknown"
    try:
        from core_v2.config import Settings
        from sqlalchemy.engine import make_url
        settings = Settings.load()
        checks["configuration"] = result("error" if settings.music else "ok",
                                          "music_not_integrated" if settings.music else "configuration_valid_locally")
        url = make_url(settings.database)
        if url.drivername == "sqlite+aiosqlite" and url.database and url.database != ":memory:" and not url.query:
            backend = "sqlite"
            path = Path(url.database)
            checks["database"] = sqlite_check(path if path.is_absolute() else root / path)
        elif url.drivername == "postgresql+asyncpg":
            backend = "postgresql"
            checks["database"] = result("unknown", "postgresql_not_probed_offline")
        else:
            checks["database"] = result("error", "database_url_unsupported")
    except Exception:
        checks["configuration"] = result("error", "configuration_invalid")
        checks["database"] = result("unknown", "configuration_required")
    try:
        state = process.state(root)
        status = state["status"]
        running = process.locked(root)
        runtime = {"running": running, "discord_ready": running and status == "Готовий",
                   "responding": running and status in {"Готовий", "Підключення", "Відновлення після помилки"}}
    except Exception:
        runtime = {"running": None, "discord_ready": None, "responding": None}
    return {"report_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
            "os": platform.system(), "python": platform.python_version(), "packages": packages,
            "backend": backend, "checks": checks, "runtime": runtime,
            "local_checks_passed": all(item["status"] == "ok" for item in checks.values()),
            "discord_api_checked": False}


def export(report, root=ROOT):
    folder = Path(root) / "data/v2/diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / "latest.json"
    fd, temporary = tempfile.mkstemp(prefix=".report-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(report, target, ensure_ascii=False, indent=2)
            target.write("\n")
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def main():
    try:
        report = collect()
        for name, check in report["checks"].items():
            print(f"{name}: {check['status']} ({check['code']})")
        print("Discord API: не перевірено; локальна перевірка не підтверджує чинність токена.")
        if "--export" in sys.argv[1:]:
            export(report)
            print("Звіт: data/v2/diagnostics/latest.json (без токена, URL БД, ID серверів і журналів).")
        return 0 if report["local_checks_passed"] else 1
    except Exception:
        print("Діагностика не завершена. Перевірте залежності та права доступу до файлів.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
