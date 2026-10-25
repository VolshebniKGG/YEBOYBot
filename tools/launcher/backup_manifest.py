"""Versioned metadata for immutable SQLite snapshots, without configuration."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing

from core_v2.db import sqlite_version, SUPPORTED_SCHEMA_VERSIONS


class ManifestError(ValueError):
    pass


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


def build(path):
    size, digest = fingerprint(path)
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        with closing(connection.cursor()) as cursor:
            version = sqlite_version(cursor)
    if not version:
        raise ManifestError("Копія не містить підтримуваної схеми Core V2.")
    return {"format_version": 1, "backend": "sqlite", "schema_version": version,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "size_bytes": size, "sha256": digest}


def read(path):
    manifest = sidecar(path)
    if manifest.is_symlink():
        raise ManifestError("Маніфест-посилання не підтримується.")
    if not manifest.exists():
        return None
    if not manifest.is_file() or manifest.stat().st_size > 8192:
        raise ManifestError("Неправильний маніфест резервної копії.")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        expected = {"format_version", "backend", "schema_version", "created_utc", "size_bytes", "sha256"}
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError
        if type(data["format_version"]) is not int or data["format_version"] != 1:
            raise ValueError
        if type(data["schema_version"]) is not int or data["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS or data["backend"] != "sqlite":
            raise ValueError
        if type(data["size_bytes"]) is not int or data["size_bytes"] <= 0:
            raise ValueError
        if not isinstance(data["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["sha256"]):
            raise ValueError
        date = datetime.fromisoformat(data["created_utc"])
        if date.utcoffset() is None or date.utcoffset().total_seconds() != 0:
            raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError) as exc:
        raise ManifestError("Неправильний маніфест резервної копії.") from exc
    return data


def verify(path, data):
    size, digest = fingerprint(path)
    if size != data["size_bytes"] or digest != data["sha256"]:
        raise ManifestError("Розмір або SHA-256 резервної копії не збігається з маніфестом.")
