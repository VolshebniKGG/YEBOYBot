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
PACKAGES = ("discord.py", "SQLAlchemy", "aiosqlite", "asyncpg", "python-dotenv", "Pillow", "PyNaCl", "davey", "yt-dlp", "yt-dlp-ejs")


def result(status, code):
    return {"status": status, "code": code}


def sqlite_check(path):
    """No database creation, migrations, WAL mode changes, or writes."""
    if not path.is_file():
        return result("error", "database_missing")
    deadline = time.monotonic() + 2
    try:
        from core_v2.db import sqlite_version, CURRENT_SCHEMA_VERSION
        from contextlib import closing
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            try:
                db.execute("PRAGMA query_only=ON")
                if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    return result("error", "integrity_failed")
                try:
                    with closing(db.cursor()) as cursor:
                        version = sqlite_version(cursor)
                except RuntimeError:
                    return result("error", "schema_unsupported")
                if not version:
                    return result("error", "schema_incomplete")
                if version != CURRENT_SCHEMA_VERSION:
                    return result("error", "migration_required")
            finally:
                db.set_progress_handler(None, 0)
        return result("ok", f"sqlite_schema_v{version}")
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
    for module in ("discord", "sqlalchemy", "aiosqlite", "asyncpg", "dotenv", "PIL", "nacl", "davey", "yt_dlp", "yt_dlp_ejs"):
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
        checks["configuration"] = result("ok", "configuration_valid_locally")
        if settings.music:
            try:
                from core_v2.media_environment import check_music
                from core_v2.music_options import load_music_options
                check_music(settings.ffmpeg, root)
                load_music_options(root)
                checks["music"] = result("ok", "music_local_prerequisites_only")
            except Exception:
                checks["music"] = result("error", "music_prerequisites_invalid")
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
        if report["checks"]["database"]["code"] == "migration_required":
            print("Потрібна міграція SQLite через [21] після зупинки [3].")
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
