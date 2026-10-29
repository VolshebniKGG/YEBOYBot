from contextlib import closing
import sqlite3

from core_v2 import migrations
from core_v2.doctor import sqlite_preflight
from test_migrations_core import create_legacy_v1


def test_doctor_blocks_legacy_without_mutation(tmp_path):
    path = create_legacy_v1(tmp_path / "old.db")
    before = path.read_bytes()
    assert "[21]" in sqlite_preflight("sqlite+aiosqlite:///" + str(path))
    assert path.read_bytes() == before


def test_doctor_accepts_migrated_file(tmp_path):
    path = create_legacy_v1(tmp_path / "migrated.db")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        migrations.migrate_sqlite(connection)
        connection.commit()
    assert sqlite_preflight("sqlite+aiosqlite:///" + str(path)) is None


def test_doctor_allows_fresh_missing_database_without_creating_it(tmp_path):
    path = tmp_path / "missing.db"
    assert sqlite_preflight("sqlite+aiosqlite:///" + str(path)) is None
    assert not path.exists()


def test_doctor_refuses_unknown_version_without_mutation(tmp_path):
    path = create_legacy_v1(tmp_path / "unknown.db")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE v2_schema SET version=999")
        connection.commit()
    before = path.read_bytes()
    assert sqlite_preflight("sqlite+aiosqlite:///" + str(path)) is not None
    assert path.read_bytes() == before
