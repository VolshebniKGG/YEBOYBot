"""Local SQLite maintenance; no tokens are copied into backups."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import json
import shutil
import tempfile
import uuid

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from core_v2.backup import backup
from core_v2.db import sqlite_version
from core_v2.runtime import InstanceLock
from . import backup_manifest as manifest

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


def validate(path, *, snapshot=False):
    path = Path(path).resolve()
    if not path.is_file():
        raise StorageError("Файл бази даних не знайдено.")
    query = "?mode=ro&immutable=1" if snapshot else "?mode=ro"
    with closing(sqlite3.connect(path.as_uri() + query, uri=True)) as conn:
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise StorageError("Перевірка цілісності не пройдена.")
        with closing(conn.cursor()) as cursor:
            version = sqlite_version(cursor)
        if not version:
            raise StorageError("Файл не містить підтримуваної схеми Core V2.")
    return version


def backup_folder(root):
    folder = Path(root) / "backups"
    if folder.is_symlink():
        raise StorageError("Каталог backups не може бути символьним посиланням.")
    return folder


def maintenance_lock(root):
    return InstanceLock(Path(root) / "data/v2/backup.lock")


def _create(root, *, source=None):
    source = database_path(root) if source is None else Path(source).resolve()
    folder = backup_folder(root)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Incomplete copies never appear in available(). Publish under the shared lock.
    with tempfile.TemporaryDirectory(prefix=".creating-", dir=folder) as temporary:
        staged = Path(temporary) / "snapshot.db"
        backup(source, staged)
        validate(staged, snapshot=True)
        metadata = manifest.build(staged)
        manifest.sidecar(staged).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        destination = folder / ("v2-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") +
                                "-" + uuid.uuid4().hex + ".db")
        staged.rename(destination)
        try:
            manifest.sidecar(staged).rename(manifest.sidecar(destination))
        except OSError:
            # Publication failed: no half-published copy in the normal catalog.
            destination.rename(staged)
            raise
    return destination


def create(root=ROOT):
    with maintenance_lock(root):
        return _create(root)


def available(root=ROOT):
    folder = backup_folder(root)
    paths = list(folder.glob("v2-*.db"))
    archive_folder = folder / "archive"
    if archive_folder.is_symlink():
        raise StorageError("Каталог архіву не може бути символьним посиланням.")
    if archive_folder.is_dir():
        for group in archive_folder.iterdir():
            if group.is_dir() and not group.is_symlink():
                paths.extend(group.glob("v2-*.db"))
    return sorted((p for p in paths if p.is_file() and not p.is_symlink()), reverse=True)


def inspect_backup(path):
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file():
            raise StorageError("Копія-посилання не підтримується.")
        # Snapshots are closed, standalone files. Never ignore an external writer's WAL.
        if any(p.exists() and p.stat().st_size for p in
               (Path(str(path) + "-wal"), Path(str(path) + "-journal"))):
            raise StorageError("Копія має активні SQLite sidecar-файли.")
        metadata = manifest.read(path)
        if metadata is not None:
            manifest.verify(path, metadata)
        version = validate(path, snapshot=True)
        if metadata is not None and metadata["schema_version"] != version:
            raise StorageError("Версія маніфесту не відповідає копії.")
        if metadata is None:
            return {"status": "legacy", "size_bytes": path.stat().st_size,
                    "created_utc": None, "sha256": None}
        return dict(metadata, status="verified")
    except (OSError, ValueError, StorageError, sqlite3.Error):
        return {"status": "invalid", "size_bytes": None, "created_utc": None, "sha256": None}


def _require_verified(path, *, allow_legacy=False):
    info = inspect_backup(path)
    if info["status"] == "invalid":
        raise StorageError("Копія пошкоджена, її маніфест неправильний або SHA-256 не збігається.")
    if info["status"] == "legacy" and not allow_legacy:
        raise StorageError("Стара копія без SHA-256 потребує окремого підтвердження.")
    return info


def _same_file(first, second):
    return first.resolve() == second.resolve() or (second.exists() and first.samefile(second))


def restore(source, *, root=ROOT, confirmed=False, allow_legacy=False):
    if not confirmed:
        raise StorageError("Відновлення потребує явного підтвердження.")
    source = Path(source).absolute()
    # Same lock as managed and direct core entry points, then shared backup lock.
    with InstanceLock(Path(root) / "data/v2/bot.lock"), maintenance_lock(root):
        if source not in [p.absolute() for p in available(root)]:
            raise StorageError("Виберіть копію зі списку цього проєкту.")
        target = database_path(root)
        if _same_file(source, target):
            raise StorageError("Джерело й поточна база збігаються.")
        info = _require_verified(source, allow_legacy=allow_legacy)
        # Verify the actual bytes to restore, not just a path before a later read.
        with tempfile.TemporaryDirectory(prefix=".restoring-", dir=backup_folder(root)) as temporary:
            staged = Path(temporary) / "snapshot.db"
            shutil.copyfile(source, staged)
            if info["status"] == "verified":
                manifest.verify(staged, info)
            validate(staged, snapshot=True)
            previous = _create(root, source=target) if target.exists() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(staged.as_uri() + "?mode=ro&immutable=1", uri=True)) as original:
                with closing(sqlite3.connect(target)) as destination:
                    original.backup(destination)
            validate(target)
            return previous


def retention_plan(root=ROOT, keep=10):
    if type(keep) is not int or not 1 <= keep <= 10000:
        raise StorageError("Потрібно залишити від 1 до 10000 останніх перевірених копій.")
    target = database_path(root)
    folder = backup_folder(root)
    verified = []
    for path in available(root):
        if path.parent != folder or _same_file(path, target):
            continue
        info = inspect_backup(path)
        if info["status"] == "verified":
            verified.append((datetime.fromisoformat(info["created_utc"]), path))
    verified.sort(reverse=True)
    return [path for _, path in verified[keep:]]


def archive(candidates, *, root=ROOT, confirmed=False, keep=10):
    if not confirmed:
        raise StorageError("Архівування потребує явного підтвердження.")
    candidates = [Path(p) for p in candidates]
    with maintenance_lock(root):
        allowed = set(retention_plan(root, keep))
        if len(set(candidates)) != len(candidates) or not set(candidates) <= allowed:
            raise StorageError("Список копій змінився або містить захищені файли. Повторіть перегляд.")
        archived = []
        for path in candidates:
            _require_verified(path)
            group = backup_folder(root) / "archive" / uuid.uuid4().hex
            group.mkdir(mode=0o700, parents=True)
            destination = group / path.name
            path.rename(destination)
            try:
                manifest.sidecar(path).rename(manifest.sidecar(destination))
            except OSError:
                # Keep an accessible original pair if the second move fails.
                destination.rename(path)
                group.rmdir()
                raise
            archived.append(destination)
        return archived


def description(path):
    info = inspect_backup(path)
    status = {"verified": "Перевірено", "legacy": "Стара копія без SHA-256", "invalid": "Перевірку не пройдено"}[info["status"]]
    size = str(info["size_bytes"]) if info["size_bytes"] is not None else "Невідомо"
    return (f"{status} | {info['created_utc'] or 'Дата невідома'} | {size} байт | "
            f"SHA-256: {info['sha256'] or 'Невідомо'}")


def catalog_menu(root=ROOT):
    with maintenance_lock(root):
        copies = available(root)
        for path in copies:
            print(f"{path.relative_to(backup_folder(root))} | {description(path)}")
        if not copies:
            print("Резервних копій немає.")
    value = input("Залишити скільки останніх перевірених копій? Enter — лише перегляд: ").strip()
    if not value:
        return
    if not value.isdecimal():
        raise StorageError("Вкажіть кількість цілим числом.")
    keep = int(value)
    with maintenance_lock(root):
        candidates = retention_plan(root, keep)
    if not candidates:
        print("Копій для архівування немає. Старі й неперевірені копії не переміщуються.")
        return
    print("Буде перенесено в backups/archive/ без видалення даних:")
    for path in candidates:
        print(path.name)
    if input("Для підтвердження введіть АРХІВУВАТИ: ").strip() != "АРХІВУВАТИ":
        print("Скасовано.")
        return
    print("Архівовано:", len(archive(candidates, root=root, confirmed=True, keep=keep)))


def restore_menu(root=ROOT):
    copies = available(root)
    if not copies:
        print("Резервних копій немає.")
        return
    for i, path in enumerate(copies, 1):
        print(f"[{i}] {path.relative_to(backup_folder(root))} | {description(path)}")
    value = input("Номер копії (Enter — скасувати): ").strip()
    if not value:
        return
    if not value.isdigit() or not 1 <= int(value) <= len(copies):
        raise StorageError("Неправильний номер.")
    chosen = copies[int(value)-1]
    info = _require_verified(chosen, allow_legacy=True)
    allow_legacy = False
    if info["status"] == "legacy":
        print("Стара копія без маніфесту: дата й початкова SHA-256 невідомі.")
        if input("Для дозволу введіть СТАРА КОПІЯ: ").strip() != "СТАРА КОПІЯ":
            print("Скасовано.")
            return
        allow_legacy = True
    print("Зупиніть бот через [3]. Поточні дані буде замінено; спочатку створиться їх резервна копія.")
    if input("Для підтвердження введіть ВІДНОВИТИ: ").strip() != "ВІДНОВИТИ":
        print("Скасовано.")
        return
    previous = restore(chosen, root=root, confirmed=True, allow_legacy=allow_legacy)
    print("Відновлення завершено. Попередня копія:", previous or "база була відсутня")


def check(root=ROOT):
    validate(database_path(root))
    print("SQLite: цілісність і підтримувана схема перевірені. Дані не змінено.")

