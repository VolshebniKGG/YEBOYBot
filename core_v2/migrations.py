"""Explicit ordered SQLite upgrades; caller owns lock, verified backup and transaction."""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .db import CURRENT_SCHEMA_VERSION, sqlite_version


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Revision:
    source: int
    target: int
    name: str


REVISIONS = (Revision(1, 2, "002_case_updated_at"),)


def inspect_sqlite(connection):
    """Inspect fresh/v1/v2 without changing journal mode, schema or data."""
    cursor = connection.cursor()
    try:
        return sqlite_version(cursor)
    finally:
        cursor.close()


def migration_path(current, target=CURRENT_SCHEMA_VERSION):
    if type(target) is not int or target != CURRENT_SCHEMA_VERSION:
        raise MigrationError("Migration target is not supported; automatic downgrade is forbidden")
    if type(current) is not int or current < 1 or current > target:
        raise MigrationError("Unsupported migration source; bootstrap fresh databases through startup")
    pending = []
    while current != target:
        candidates = [revision for revision in REVISIONS if revision.source == current]
        if len(candidates) != 1 or candidates[0].target != current + 1:
            raise MigrationError("Migration chain is incomplete or ambiguous")
        revision = candidates[0]
        pending.append(revision)
        current = revision.target
    return tuple(pending)


def plan_sqlite(path, target=CURRENT_SCHEMA_VERSION):
    """Read-only plan of a file; a missing database is never created."""
    path = Path(path).resolve()
    if not path.is_file():
        raise MigrationError("Database file does not exist")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        current = inspect_sqlite(connection)
    revisions = migration_path(current, target)
    return {"current": current, "target": target,
            "revisions": [revision.name for revision in revisions], "requires_backup": bool(revisions)}


def _apply_revision(connection, revision):
    if revision.name != "002_case_updated_at" or revision.source != 1 or revision.target != 2:
        raise MigrationError("Unknown migration revision")
    connection.execute("ALTER TABLE v2_cases ADD COLUMN updated FLOAT NOT NULL DEFAULT 0")
    connection.execute("UPDATE v2_cases SET updated=created")


def migrate_sqlite(connection, target=CURRENT_SCHEMA_VERSION):
    """Apply within caller's existing transaction; never commits or rolls it back."""
    if not connection.in_transaction:
        raise MigrationError("Migration requires an explicit caller-owned transaction")
    current = inspect_sqlite(connection)
    pending = migration_path(current, target)
    applied = []
    for revision in pending:
        _apply_revision(connection, revision)
        cursor = connection.execute("UPDATE v2_schema SET version=? WHERE version=?", (revision.target, revision.source))
        if cursor.rowcount != 1:
            raise MigrationError("Schema marker changed during migration")
        if inspect_sqlite(connection) != revision.target:
            raise MigrationError("Post-migration schema validation failed")
        applied.append(revision.name)
    return applied
