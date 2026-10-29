"""Bounded native PostgreSQL snapshots; connection credentials never enter argv."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy.engine import make_url

from core_v2.db import SUPPORTED_SCHEMA_VERSIONS, meta

TABLES = tuple(sorted(table.name for table in meta.sorted_tables))
TIMEOUT = 180


class PostgresError(RuntimeError):
    """Safe user-visible text, without libpq output or connection URLs."""


def _base_env():
    return {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}


def connection_env(url):
    """Translate the supported asyncpg URL into libpq environment only."""
    try:
        parsed = make_url(url)
        if isinstance(url, str) and any(not value for _, value in parse_qsl(urlsplit(url).query, keep_blank_values=True)):
            raise ValueError
        if (parsed.drivername != "postgresql+asyncpg" or not parsed.host or
                not parsed.database or not parsed.username or parsed.host.startswith("/")):
            raise ValueError
        # asyncpg accepts ssl=mode, but certificate-path URL parameters are not
        # shared with SQLAlchemy's asyncpg connection API. Reject them rather
        # than making dump and application sessions use different TLS settings.
        mapping = {"ssl": "PGSSLMODE"}
        if set(parsed.query) - mapping.keys():
            raise ValueError
        if any(not isinstance(value, str) or not value for value in parsed.query.values()):
            raise ValueError
        if "ssl" in parsed.query and parsed.query["ssl"] not in {
                "disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise ValueError
        values = {"PGHOST": parsed.host, "PGPORT": str(parsed.port or 5432),
                  "PGDATABASE": parsed.database, "PGUSER": parsed.username,
                  "PGPASSWORD": parsed.password or "", "PGCONNECT_TIMEOUT": "10"}
        values.update({mapping[key]: value for key, value in parsed.query.items()})
        if any("\0" in value or "\n" in value or "\r" in value for value in values.values()):
            raise ValueError
        env = _base_env()
        env.update(values)
        return env
    except Exception:
        # Parser messages can contain the entire URL/password.
        raise PostgresError("Потрібен підтримуваний PostgreSQL TCP URL; перевірте параметри підключення та SSL.") from None


def _executable(name):
    executable = shutil.which(name)
    if not executable:
        raise PostgresError("Встановіть штатні PostgreSQL client tools pg_dump і pg_restore та додайте їх у PATH.")
    return executable


def _run(name, args, *, env=None, stdin=None, stdout=subprocess.PIPE):
    try:
        result = subprocess.run([_executable(name), *args], env=_base_env() if env is None else env,
                                stdin=stdin, stdout=stdout, stderr=subprocess.PIPE,
                                timeout=TIMEOUT, check=False)
    except PostgresError:
        raise
    except (OSError, subprocess.SubprocessError):
        raise PostgresError("PostgreSQL client tools не завершили операцію вчасно; перевірте доступ, мережу та диск.") from None
    if result.returncode:
        # Never print libpq stderr: it may repeat credentials, URLs or database data.
        raise PostgresError("PostgreSQL client tools відхилили операцію; перевірте дозволи, версії й підключення.")
    return result.stdout or b""


def tool_versions(server_major=None):
    versions = {}
    for name in ("pg_dump", "pg_restore"):
        output = _run(name, ["--version"]).decode("ascii", errors="replace")
        match = re.search(r"\bPostgreSQL\)?\s+(\d+)(?:\.\d+)*", output)
        if not match:
            raise PostgresError("Не вдалося визначити версію PostgreSQL client tools.")
        versions[name] = int(match[1])
    if versions["pg_dump"] != versions["pg_restore"]:
        raise PostgresError("pg_dump і pg_restore повинні мати однакову major-версію.")
    if server_major is not None and (type(server_major) is not int or versions["pg_dump"] < server_major):
        raise PostgresError("Версія PostgreSQL client tools старіша за сервер або не підтримується.")
    return versions


def sidecar(path):
    return Path(str(path) + ".json")


def fingerprint(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _plain_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PostgresError("Резервна копія та маніфест мають бути звичайними файлами, не посиланнями.")
    return path


def _read_manifest(path):
    manifest = _plain_file(sidecar(path))
    try:
        if manifest.stat().st_size > 8192:
            raise ValueError
        data = json.loads(manifest.read_text(encoding="utf-8"))
        expected = {"format_version", "backend", "schema_version", "created_utc",
                    "size_bytes", "sha256", "pg_dump_major", "tables"}
        if type(data) is not dict or set(data) != expected:
            raise ValueError
        if type(data["format_version"]) is not int or data["format_version"] != 1 or data["backend"] != "postgresql":
            raise ValueError
        if type(data["schema_version"]) is not int or data["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError
        if type(data["size_bytes"]) is not int or data["size_bytes"] <= 5:
            raise ValueError
        if type(data["pg_dump_major"]) is not int or not 10 <= data["pg_dump_major"] < 100:
            raise ValueError
        if not isinstance(data["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["sha256"]):
            raise ValueError
        if data["tables"] != list(TABLES):
            raise ValueError
        date = datetime.fromisoformat(data["created_utc"])
        if date.utcoffset() is None or date.utcoffset().total_seconds() != 0:
            raise ValueError
        return data
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        raise PostgresError("Некоректний маніфест PostgreSQL-копії.") from None


def _check_listing(output):
    tables = []
    for line in output.decode("utf-8", errors="replace").splitlines():
        match = re.match(r"\d+;\s+\d+\s+\d+\s+TABLE\s+(?!DATA\s)(\S+)\s+(\S+)(?:\s|$)", line)
        if match:
            schema, name = match.groups()
            if schema != "public":
                raise PostgresError("Архів містить таблиці поза підтримуваною public схемою.")
            tables.append(name)
    if sorted(tables) != list(TABLES):
        raise PostgresError("Архів не містить рівно підтримувані таблиці Core V2.")


def verify_archive(path):
    """Verify integrity and native TOC; use only trusted locally created archives."""
    try:
        path = _plain_file(path)
        data = _read_manifest(path)
        with path.open("rb") as source:
            if source.read(5) != b"PGDMP":
                raise PostgresError("Файл не є PostgreSQL custom archive.")
        size, digest = fingerprint(path)
        if size != data["size_bytes"] or digest != data["sha256"]:
            raise PostgresError("Розмір або SHA-256 PostgreSQL-копії не відповідає маніфесту.")
        versions = tool_versions()
        if versions["pg_restore"] < data["pg_dump_major"]:
            raise PostgresError("pg_restore старіший за версію, яка створила копію.")
        with path.open("rb") as source:
            _check_listing(_run("pg_restore", ["--list"], stdin=source))
        return data
    except PostgresError:
        raise
    except OSError:
        raise PostgresError("Не вдалося прочитати PostgreSQL-копію або маніфест.") from None


def dump(url, destination, schema_version):
    """Create a new private archive; caller holds source table/maintenance locks."""
    if type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise PostgresError("Непідтримувана версія схеми PostgreSQL-копії.")
    env = connection_env(url)
    major = tool_versions()["pg_dump"]
    destination = Path(destination)
    manifest = sidecar(destination)
    # Refuse both members before opening either. O_EXCL also catches races/links.
    if destination.exists() or destination.is_symlink() or manifest.exists() or manifest.is_symlink():
        raise PostgresError("Шлях копії або маніфест уже зайнятий; перезапис заборонено.")
    created = []
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created.append(destination)
        with os.fdopen(descriptor, "wb") as target:
            args = ["--format=custom", "--no-owner", "--no-acl", "--no-password", "--no-tablespaces"]
            args.extend("--table=public." + name for name in TABLES)
            _run("pg_dump", args, env=env, stdout=target)
        size, digest = fingerprint(destination)
        data = {"format_version": 1, "backend": "postgresql", "schema_version": schema_version,
                "created_utc": datetime.now(timezone.utc).isoformat(), "size_bytes": size,
                "sha256": digest, "pg_dump_major": major, "tables": list(TABLES)}
        descriptor = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created.append(manifest)
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(data, target, ensure_ascii=False, indent=2)
            target.write("\n")
        return verify_archive(destination)
    except Exception as error:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        if isinstance(error, PostgresError):
            raise
        raise PostgresError("Не вдалося створити PostgreSQL-копію; перевірте вільне місце й права доступу.") from None


def restore(url, path):
    """Restore into caller-verified empty target; never clean/drop/overwrite objects."""
    data = verify_archive(path)
    env = connection_env(url)
    try:
        with Path(path).open("rb") as source:
            _run("pg_restore", ["--single-transaction", "--exit-on-error", "--no-owner",
                                "--no-acl", "--no-password", "--no-tablespaces", "--dbname="], env=env, stdin=source)
    except OSError:
        raise PostgresError("Не вдалося прочитати PostgreSQL-копію для відновлення.") from None
    return data
