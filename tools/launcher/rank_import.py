"""Explicit, backed-up import of one guild's legacy XP into local Core V2."""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat

from sqlalchemy.exc import ArgumentError

from core_v2.db import CURRENT_SCHEMA_VERSION, sqlite_version
from core_v2.runtime import InstanceLock
from . import storage

ROOT = Path(__file__).resolve().parents[2]
MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000
MAX_INTEGER = 2**63 - 1
POLICY = "max_per_counter"


class RankImportError(RuntimeError):
    pass


def _identifier(value):
    if type(value) is int:
        if 0 < value <= MAX_INTEGER:
            return value
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,18}", value):
        number = int(value)
        if number <= MAX_INTEGER:
            return number
    raise RankImportError("ID сервера й користувачів мають бути додатними ASCII-числами до 2^63−1.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RankImportError("JSON містить повторювані ключі. Імпорт скасовано.")
        result[key] = value
    return result


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise RankImportError("JSON містить нескінченне число або NaN.")
    return number


def _reject_constant(_value):
    raise RankImportError("JSON містить нескінченне число або NaN.")


def _counter(value):
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise RankImportError("Усі лічильники XP мають бути цілими числами від 0 до 2^63−1.")
    return value


def _file_identity(path):
    info = path.stat()
    return hashlib.sha256(
        (str(path.resolve()) + f"\0{info.st_dev}\0{info.st_ino}").encode("utf-8")
    ).hexdigest()


def _read_source(root, guild_id):
    source = Path(root) / "data/rank" / str(guild_id) / "levels.json"
    try:
        if source.is_symlink() or source.parent.is_symlink():
            raise RankImportError("Джерело XP не може бути символьним посиланням.")
        info = source.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES:
            raise RankImportError("Потрібен звичайний levels.json розміром не більш як 10 MiB.")
        with source.open("rb") as handle:
            # fstat binds the size check to the opened file, not an earlier path.
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SOURCE_BYTES:
                raise RankImportError("Потрібен звичайний levels.json розміром не більш як 10 MiB.")
            raw = handle.read(MAX_SOURCE_BYTES + 1)
            after = os.fstat(handle.fileno())
        current = source.stat()
        def signature(info):
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
        if len(raw) > MAX_SOURCE_BYTES:
            raise RankImportError("levels.json перевищує 10 MiB.")
        if signature(before) != signature(after) or signature(after) != signature(current):
            raise RankImportError("Файл XP змінився під час читання. Повторіть перегляд.")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_float=_finite_float, parse_constant=_reject_constant)
    except RankImportError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise RankImportError("Не вдалося прочитати коректний UTF-8 JSON із data/rank/<ID>/levels.json.") from error
    if not isinstance(data, dict) or len(data) > MAX_ROWS:
        raise RankImportError("levels.json має містити об’єкт із не більш як 50000 користувачами.")
    rows = []
    for user_id, record in data.items():
        user_id = _identifier(user_id)
        if not isinstance(record, dict) or not {"xp", "xp_text", "xp_voice"}.intersection(record):
            raise RankImportError("Кожен запис користувача має містити об’єкт із лічильником XP.")
        counters = {key: _counter(record[key]) for key in ("xp", "xp_text", "xp_voice") if key in record}
        if "xp" in counters and "xp_text" in counters and counters["xp"] != counters["xp_text"]:
            raise RankImportError("Запис містить суперечливі xp та xp_text. Спочатку уточніть джерело.")
        rows.append((guild_id, user_id, counters.get("xp_text", counters.get("xp", 0)),
                     counters.get("xp_voice", 0)))
    identity = hashlib.sha256(
        (str(source.resolve()) + "\0" + "\0".join(map(str, signature(current)))).encode("utf-8")
    ).hexdigest()
    return rows, {"source_sha256": hashlib.sha256(raw).hexdigest(), "source_identity": identity}


def _database(root):
    try:
        path = storage.database_path(root)
    except (storage.StorageError, ArgumentError, ValueError) as error:
        raise RankImportError("Імпорт XP підтримує лише файлову SQLite. Для PostgreSQL потрібен окремий перевірений workflow; автоматичного імпорту немає.") from error
    try:
        version = storage.validate(path)
        if version in (1, 2):
            raise RankImportError("Спочатку явно оновіть схему SQLite v1/v2 через [21], потім повторіть імпорт XP.")
        if version != CURRENT_SCHEMA_VERSION:
            raise RankImportError("Імпорт XP потребує наявної перевіреної схеми SQLite v3.")
        return path, _file_identity(path)
    except RankImportError:
        raise
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        raise RankImportError("Потрібна наявна, ціла й підтримувана SQLite v3. Перевірте [4]/[21]; імпорт не створює й не мігрує БД.") from error


def _prepare(root, guild_id):
    guild_id = _identifier(guild_id)
    path, database_identity = _database(root)
    rows, source = _read_source(root, guild_id)
    preview = dict(source, guild_id=guild_id, row_count=len(rows), policy=POLICY,
                   database_identity=database_identity)
    return path, rows, preview


def plan(guild_id, root=ROOT):
    """Read and validate the whole source and database without changing either."""
    return _prepare(root, guild_id)[2]


def _require_same(root, guild_id, expected):
    path, rows, current = _prepare(root, guild_id)
    if current != expected:
        raise RankImportError("Файл XP, БД або план змінився. Повторіть перегляд і підтвердження.")
    return path, rows


def _merge(connection, rows):
    connection.executemany(
        "INSERT INTO v2_xp (guild_id, user_id, text_xp, voice_xp, last_text, last_voice) "
        "VALUES (?, ?, ?, ?, 0, 0) "
        "ON CONFLICT (guild_id, user_id) DO UPDATE SET "
        "text_xp = max(v2_xp.text_xp, excluded.text_xp), "
        "voice_xp = max(v2_xp.voice_xp, excluded.voice_xp)", rows)


def apply(guild_id, root=ROOT, *, confirmed=False, expected_plan=None):
    """Merge a confirmed snapshot under bot, maintenance and SQLite writer locks."""
    if not confirmed:
        raise RankImportError("Імпорт XP потребує явного підтвердження.")
    # Reject invalid input and incompatible files before even creating lock files.
    path, rows, preview = _prepare(root, guild_id)
    guild_id = preview["guild_id"]
    if expected_plan is not None and preview != expected_plan:
        raise RankImportError("Файл XP, БД або план змінився. Повторіть перегляд і підтвердження.")
    try:
        with InstanceLock(Path(root) / "data/v2/bot.lock"), storage.maintenance_lock(root):
            path, rows = _require_same(root, guild_id, preview)
            # The verified recovery copy is complete before opening a write transaction.
            saved = storage._create(root, source=path)
            if storage._require_verified(saved)["schema_version"] != CURRENT_SCHEMA_VERSION:
                raise RankImportError("Схема резервної копії змінилася. Імпорт скасовано.")
            _require_same(root, guild_id, preview)
            with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True,
                                         timeout=5, isolation_level=None)) as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    with closing(connection.cursor()) as cursor:
                        if sqlite_version(cursor) != CURRENT_SCHEMA_VERSION:
                            raise RankImportError("Схема БД змінилася. Імпорт скасовано.")
                    if storage.database_path(root) != path or _file_identity(path) != preview["database_identity"]:
                        raise RankImportError("Файл або конфігурація БД змінилися. Повторіть перегляд.")
                    # Use only the validated rows whose exact bytes were confirmed.
                    current_rows, source = _read_source(root, guild_id)
                    if any(source[key] != preview[key] for key in source):
                        raise RankImportError("Файл XP змінився. Повторіть перегляд і підтвердження.")
                    _merge(connection, current_rows)
                    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise RankImportError("Перевірка після імпорту не пройдена.")
                    _, source = _read_source(root, guild_id)
                    if any(source[key] != preview[key] for key in source):
                        raise RankImportError("Файл XP змінився під час імпорту. Зміни БД скасовано.")
                    with closing(connection.cursor()) as cursor:
                        if sqlite_version(cursor) != CURRENT_SCHEMA_VERSION:
                            raise RankImportError("Схема БД змінилася. Зміни імпорту скасовано.")
                    if storage.database_path(root) != path or _file_identity(path) != preview["database_identity"]:
                        raise RankImportError("Файл або конфігурація БД змінилися. Зміни імпорту скасовано.")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            return {"status": "applied", "guild_id": guild_id, "row_count": len(rows),
                    "policy": POLICY, "backup": str(saved.relative_to(Path(root)))}
    except RankImportError:
        raise
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        raise RankImportError("Імпорт XP не виконано. Зупиніть бот через [3], перевірте [4] і повторіть перегляд; наявну резервну копію збережено.") from error


def menu(root=ROOT):
    value = input("ID сервера для імпорту data/rank/<ID>/levels.json (Enter — скасувати): ").strip()
    if not value:
        return
    preview = plan(value, root)
    print(f"Сервер: {preview['guild_id']} | Користувачів: {preview['row_count']}")
    print("Для кожного користувача: text XP = max(поточне, старе); voice XP = max(поточне, старе).")
    print("Повторний імпорт не додає XP. Час нарахувань зберігається; рівні обчислюються з XP.")
    print("Зупиніть бот через [3]. Спочатку створиться перевірена резервна копія SQLite.")
    print("JSON збережеться. Для PostgreSQL потрібен окремий перевірений workflow.")
    if input("Для підтвердження введіть ІМПОРТУВАТИ XP: ").strip() != "ІМПОРТУВАТИ XP":
        print("Скасовано.")
        return
    report = apply(value, root, confirmed=True, expected_plan=preview)
    print(f"Імпорт XP завершено: {report['row_count']} користувачів. Резервна копія: {report['backup']}")
    print("Перевірте [4], потім запустіть бот через [1].")
