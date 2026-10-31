"""Offline PostgreSQL maintenance: verified Core snapshots and empty-target recovery."""
import asyncio
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
import warnings

from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from core_v2.db import required_columns

from core_v2 import postgres_migrations as engine
from core_v2.migrations import migration_path
from core_v2.runtime import InstanceLock
from . import pg_native as native, storage

ROOT = Path(__file__).resolve().parents[2]
PostgresError = native.PostgresError


async def native_call(function, *args):
    """Retain DB locks until a bounded native worker finishes, even on Ctrl+C."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


def configured_url(root=ROOT):
    try:
        if any(key.upper().startswith("PGSSL") for key in os.environ):
            raise PostgresError("Успадковані PGSSL* потребують узгодженої TLS-конфігурації; автоматичне послаблення заборонено. Цей workflow підтримує ssl=<mode> в URL без окремих certificate-параметрів.")
        raw = os.environ.get("DATABASE_URL") or dotenv_values(Path(root) / ".env.v2").get("DATABASE_URL") or ""
        native.connection_env(raw)
        return make_url(raw)
    except PostgresError:
        raise
    except Exception:
        raise PostgresError("Налаштуйте PostgreSQL DATABASE_URL у .env.v2; це меню не змінює SQLite.") from None


def connect(url):
    # Never call Database.initialize: a maintenance preview must not bootstrap DBs.
    return create_async_engine(url, poolclass=NullPool, connect_args={"server_settings": {
        "search_path": "public", "lock_timeout": "5000", "statement_timeout": "30000"}})


async def _preview(connection, url):
    current = await engine.inspect_postgres(connection)
    revisions = migration_path(current)
    # Unknown extra Core tables might otherwise be silently omitted by the snapshot.
    objects = (await connection.execute(text(
        "SELECT relname FROM pg_class WHERE relnamespace='public'::regnamespace "
        "AND relkind IN ('r','p','v','m','f') AND relname LIKE 'v2\\_%' ESCAPE '\\'"))).scalars().all()
    if set(objects) != set(engine.tables_for_version(current)):
        raise PostgresError("Набір PostgreSQL таблиць Core V2 не підтримується; автоматичні зміни заблоковано.")
    await _standard_layout(connection, current)
    major = int(await connection.scalar(text("SHOW server_version_num"))) // 10000
    identity = (await connection.execute(text(
        "SELECT current_database(), (SELECT oid FROM pg_database WHERE datname=current_database()), "
        "inet_server_addr()::text, inet_server_port()"))).one()
    # No password or URL is placed in plans/reports. Credential rotation is permitted.
    source = (url.host, url.port or 5432, url.database, *identity)
    source_id = hashlib.sha256(repr(source).encode("utf-8")).hexdigest()
    return {"current": current, "target": engine.CURRENT_SCHEMA_VERSION, "revisions": [r.name for r in revisions],
            "source_id": source_id, "server_major": major}


async def _standard_layout(connection, version):
    """Table-scoped pg_dump must not omit custom dependencies or descendants."""
    table_ids = "SELECT oid FROM pg_class WHERE relnamespace='public'::regnamespace AND relname = ANY(:names)"
    args = {"names": list(engine.tables_for_version(version))}
    checks = [
        f"SELECT count(*) FROM pg_class WHERE oid IN ({table_ids}) AND relkind <> 'r'",
        f"SELECT count(*) FROM pg_inherits WHERE inhparent IN ({table_ids}) OR inhrelid IN ({table_ids})",
        f"SELECT count(*) FROM pg_trigger WHERE tgrelid IN ({table_ids}) AND NOT tgisinternal",
        f"SELECT count(*) FROM pg_constraint WHERE conrelid IN ({table_ids}) AND contype IN ('f','c')",
        f"SELECT count(*) FROM pg_index WHERE indrelid IN ({table_ids}) AND (indexprs IS NOT NULL OR indpred IS NOT NULL)",
        f"SELECT count(*) FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid WHERE a.attrelid IN ({table_ids}) "
        "AND a.attnum>0 AND NOT a.attisdropped AND (t.typnamespace<>'pg_catalog'::regnamespace OR a.attgenerated<>'')",
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema')",
    ]
    for query in checks:
        if await connection.scalar(text(query), args):
            raise PostgresError("Кастомні типи/функції/constraints, expression indexes, partition/inheritance або triggers не підтримуються цим Core-backup workflow.")
    actual = (await connection.execute(text(
        "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' "
        "AND table_name = ANY(:names)"), args)).all()
    columns = {name: {column for table, column in actual if table == name} for name in engine.tables_for_version(version)}
    if columns != required_columns(version):
        raise PostgresError("Кастомні колонки Core-таблиць потребують окремого повного PostgreSQL backup workflow.")
    await engine.validate_defaults(connection)


async def plan(root=ROOT):
    url = configured_url(root)
    database = connect(url)
    try:
        async with database.connect() as connection:
            return await _preview(connection, url)
    except Exception:
        raise PostgresError("План PostgreSQL не отримано: перевірте доступ і підтримувану схему v1/v2/v3. Дані не змінювалися.") from None
    finally:
        await database.dispose()


def _folder(root):
    parent = storage.backup_folder(Path(root))
    if hasattr(parent, "is_junction") and parent.is_junction():
        raise PostgresError("Каталог backups не може бути junction-посиланням.")
    folder = parent / "postgres"
    if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
        raise PostgresError("Каталог PostgreSQL-копій не може бути посиланням.")
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    return folder


def write_report(root, report):
    folder = Path(root) / "data/v2"
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pg-report-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(report, target, ensure_ascii=False, indent=2)
            target.write("\n")
        os.replace(name, folder / "postgres-report.json")
    finally:
        Path(name).unlink(missing_ok=True)


async def maintain(root=ROOT, *, upgrade=False, confirmed=False, expected_plan=None):
    """Stop bot first. Keep source locks through dump and additive transactional DDL."""
    if upgrade and not confirmed:
        raise PostgresError("Міграція PostgreSQL потребує явного підтвердження.")
    root = Path(root).resolve()
    url = configured_url(root)
    database = connect(url)
    report = {"backend": "postgresql", "status": "unchanged", "backup": None, "phase": "preflight"}
    committed = False
    commit_attempted = False
    try:
        with InstanceLock(root / "data/v2/bot.lock"), storage.maintenance_lock(root):
            async with database.begin() as connection:
                preview = await _preview(connection, url)
                if expected_plan is not None and expected_plan != preview:
                    raise PostgresError("План або PostgreSQL джерело змінилося; повторіть перегляд.")
                report.update({"from": preview["current"], "to": preview["target"], "revisions": preview["revisions"]})
                if upgrade and not preview["revisions"]:
                    return report
                await engine.lock_source(connection)
                if await _preview(connection, url) != preview or configured_url(root) != url:
                    raise PostgresError("Версія або конфігурація змінилася перед резервуванням.")
                report["phase"] = "tools"
                tools = await native_call(native.tool_versions, preview["server_major"])
                if tools["pg_dump"] != preview["server_major"]:
                    raise PostgresError("Для backup оберіть client tools тієї самої major-версії, що й PostgreSQL сервер.")
                destination = _folder(root) / ("core-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex + ".dump")
                report["phase"] = "backup"
                info = await native_call(native.dump, url, destination, preview["current"])
                if info["schema_version"] != preview["current"]:
                    raise PostgresError("Версія резервної копії не відповідає джерелу.")
                report.update(status="backed_up", backup=str(destination.relative_to(root)))
                write_report(root, report)
                if configured_url(root) != url or await _preview(connection, url) != preview:
                    raise PostgresError("Конфігурація або джерело змінилися під час резервування.")
                if upgrade:
                    report["phase"] = "migration"
                    report["revisions"] = await engine.migrate_postgres(connection)
                commit_attempted = True
                report["phase"] = "commit"
            committed = True
            report["status"] = "applied" if upgrade else "backed_up"
            report["phase"] = "complete"
            write_report(root, report)
            return report
    except asyncio.CancelledError:
        report.update(status="unknown_commit" if commit_attempted else "cancelled", error_type="CancelledError")
        try:
            write_report(root, report)
        except OSError:
            pass
        raise
    except Exception as error:
        if committed:
            raise PostgresError("PostgreSQL операція завершена, але звіт не записано. Перевірте фактичну схему перед повторенням.") from None
        report.update(status="unknown_commit" if commit_attempted else "failed", error_type=type(error).__name__)
        try:
            write_report(root, report)
        except OSError:
            pass
        raise PostgresError("PostgreSQL операцію не підтверджено. Залиште бот зупиненим і перевірте фактичну схему та postgres-report.json; результат COMMIT при втраті зв'язку може бути невідомим. Перевірена копія зберігається, якщо її створено.") from None
    finally:
        await database.dispose()


async def _empty_target(connection):
    if not await connection.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": engine.LOCK_KEY}):
        raise PostgresError("Цільова PostgreSQL зайнята іншою операцією.")
    objects = await connection.scalar(text(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' "
        "AND c.relkind IN ('r','p','v','m','f','S')"))
    functions = await connection.scalar(text(
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema')"))
    if objects or functions:
        raise PostgresError("Відновлення дозволене лише в окрему порожню PostgreSQL БД; наявні об'єкти не перезаписуються.")
    return (await connection.execute(text(
        "SELECT current_database(), inet_server_addr()::text, inet_server_port()"))).one()


async def restore_empty(root, path, target_url, *, confirmed=False):
    """No clean/drop/overwrite: recover only to a separate pre-created empty DB."""
    if not confirmed:
        raise PostgresError("Відновлення потребує явного підтвердження та довіреної власної копії.")
    root, path = Path(root).resolve(), Path(path).absolute()
    native.connection_env(target_url)
    target = make_url(target_url)
    source = configured_url(root)
    if (source.host, source.port or 5432, source.database) == (target.host, target.port or 5432, target.database):
        raise PostgresError("Відновлення в поточну БД заборонене; підготуйте окрему порожню БД.")
    if path not in list(_folder(root).glob("core-*.dump")):
        raise PostgresError("Виберіть копію з PostgreSQL-каталогу цього проєкту.")
    database = connect(target)
    restored = False
    report = {"backend": "postgresql", "status": "restore_failed", "backup": str(path.relative_to(root))}
    try:
        with InstanceLock(root / "data/v2/bot.lock"), storage.maintenance_lock(root):
            original = await native_call(native.verify_archive, path)
            # Pin the verified bytes in a private staging directory before invoking tools.
            with tempfile.TemporaryDirectory(prefix=".pg-restore-", dir=_folder(root)) as temporary:
                staged = Path(temporary) / "snapshot.dump"
                shutil.copyfile(path, staged)
                native.sidecar(staged).write_text(json.dumps(original), encoding="utf-8")
                if await native_call(native.verify_archive, staged) != original:
                    raise PostgresError("Копія змінилася після перевірки.")
                async with database.begin() as connection:
                    await _empty_target(connection)
                    major = int(await connection.scalar(text("SHOW server_version_num"))) // 10000
                    if major < original["pg_dump_major"]:
                        raise PostgresError("Цільовий сервер старіший за версію копії; downgrade recovery не підтримується.")
                    await native_call(native.tool_versions, major)
                    await native_call(native.restore, target, staged)
                    restored = True
                    if await engine.inspect_postgres(connection) != original["schema_version"]:
                        raise PostgresError("Фактична відновлена схема не відповідає маніфесту.")
                report.update(status="restored", schema_version=original["schema_version"])
                write_report(root, report)
                return report
    except asyncio.CancelledError:
        report.update(status="restore_unknown", error_type="CancelledError")
        try:
            write_report(root, report)
        except OSError:
            pass
        raise
    except Exception as error:
        report["status"] = "restored_unverified" if restored else "restore_failed"
        report["error_type"] = type(error).__name__
        try:
            write_report(root, report)
        except OSError:
            pass
        raise PostgresError("Recovery не підтверджено. Ціль могла бути відновлена; не видаляйте її, перевірте postgres-report.json і схему. Поточна БД не перезаписувалась.") from None
    finally:
        await database.dispose()


def menu(root=ROOT):
    while True:
        print("\n=== PostgreSQL Core V2 (public) ===\n[1] План міграції\n[2] Резервна копія Core-таблиць\n[3] Міграція v1/v2 → v3\n[4] Відновити у нову порожню БД\n[0] Назад")
        choice = input("Дія: ").strip()
        if choice == "0":
            return
        try:
            if choice == "1":
                preview = asyncio.run(plan(root))
                print(f"Схема: v{preview['current']} → v{preview['target']}; ревізії: {', '.join(preview['revisions']) or 'немає'}")
                print("Client tools:", native.tool_versions(preview["server_major"]))
            elif choice == "2":
                print(asyncio.run(maintain(root))["backup"])
            elif choice == "3":
                preview = asyncio.run(plan(root))
                if not preview["revisions"]:
                    print("Схема актуальна.")
                    continue
                print("Зупиніть усі копії бота. Спершу створиться перевірена Core-копія; зміна буде транзакційною.")
                if input("Введіть МІГРУВАТИ POSTGRESQL: ").strip() == "МІГРУВАТИ POSTGRESQL":
                    print(asyncio.run(maintain(root, upgrade=True, confirmed=True, expected_plan=preview))["status"])
            elif choice == "4":
                paths = sorted(_folder(root).glob("core-*.dump"))
                for index, path in enumerate(paths, 1):
                    info = native.verify_archive(path)
                    print(f"[{index}] {path.name} | v{info['schema_version']} | {info['created_utc']} | {info['size_bytes']} байт | SHA-256 {info['sha256']}")
                number = input("Номер копії (Enter — скасувати): ").strip()
                if not number:
                    continue
                if not number.isdecimal() or not 1 <= int(number) <= len(paths):
                    raise PostgresError("Неправильний номер копії.")
                print("Тільки власні довірені копії. Підготуйте окрему порожню PostgreSQL БД; поточна не замінюється.")
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    target = getpass.getpass("URL порожньої цільової БД (приховано): ").strip()
                if input("Введіть ВІДНОВИТИ POSTGRESQL: ").strip() == "ВІДНОВИТИ POSTGRESQL":
                    print(asyncio.run(restore_empty(root, paths[int(number)-1], target, confirmed=True))["status"])
                    print("Перевірте ціль. Для переходу змініть DATABASE_URL вручну; v1 потребує [23] → [3].")
            else:
                print("Невідома дія.")
        except KeyboardInterrupt:
            print("Операцію перервано. Перевірте фактичну схему та postgres-report.json перед повторенням.")
            return
        except getpass.GetPassWarning:
            print("Приховане введення недоступне; відкрийте YEBOYBot.cmd у CMD.")
        except PostgresError as error:
            print(str(error))
        except Exception:
            print("PostgreSQL операцію не завершено. Перевірте доступ, client tools та схему; credentials не виводяться.")
