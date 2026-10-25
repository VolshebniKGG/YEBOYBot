from contextlib import closing
import sqlite3

import pytest

from core_v2.db import Database
from core_v2.runtime import InstanceLock
from tools.launcher.storage import create, restore, validate, database_path, StorageError


async def prepare(root):
    path = root / "custom folder" / "bot.db"
    (root / ".env.v2").write_text("DATABASE_URL=sqlite+aiosqlite:///custom folder/bot.db\nDISCORD_TOKEN=secret\n")
    db = Database(f"sqlite+aiosqlite:///{path}")
    await db.initialize()
    await db.configure(123, levels=False)
    await db.close()
    return path


async def test_roundtrip_with_custom_path_and_pre_restore_backup(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = await prepare(tmp_path)
    saved = create(tmp_path)
    assert database_path(tmp_path) == path
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE v2_settings SET levels=1")
        conn.commit()
    previous = restore(saved, root=tmp_path, confirmed=True)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT levels FROM v2_settings").fetchone() == (0,)
    with closing(sqlite3.connect(previous)) as conn:
        assert conn.execute("SELECT levels FROM v2_settings").fetchone() == (1,)
    assert b"DISCORD_TOKEN" not in saved.read_bytes()


async def test_restore_refuses_running_bot(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    await prepare(tmp_path)
    saved = create(tmp_path)
    with InstanceLock(tmp_path / "data/v2/bot.lock"):
        with pytest.raises(RuntimeError):
            restore(saved, root=tmp_path, confirmed=True)


async def test_future_schema_and_no_confirmation_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = await prepare(tmp_path)
    saved = create(tmp_path)
    before = path.read_bytes()
    with pytest.raises(StorageError):
        restore(saved, root=tmp_path)
    with closing(sqlite3.connect(saved)) as conn:
        conn.execute("UPDATE v2_schema SET version=999")
        conn.commit()
    with pytest.raises(StorageError):
        restore(saved, root=tmp_path, confirmed=True)
    assert path.read_bytes() == before


def test_postgres_not_silently_backed_up_as_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:secret@localhost/bot")
    with pytest.raises(StorageError):
        database_path(tmp_path)


def test_invalid_database_rejected(tmp_path):
    path = tmp_path / "bad.db"
    path.write_bytes(b"not sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        validate(path)



async def test_online_wal_backup_captures_committed_snapshot(tmp_path, monkeypatch):
    from tools.launcher.storage import inspect_backup
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = await prepare(tmp_path)
    with closing(sqlite3.connect(path)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE v2_settings SET levels=1")
        writer.commit()
        writer.execute("UPDATE v2_settings SET levels=0")
        saved = create(tmp_path)
        assert inspect_backup(saved)["status"] == "verified"
        with closing(sqlite3.connect(saved)) as snapshot:
            assert snapshot.execute("SELECT levels FROM v2_settings").fetchone() == (1,)
            assert snapshot.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        writer.rollback()
    assert inspect_backup(saved)["status"] == "verified"
    assert not saved.with_suffix(".db-wal").exists()


async def test_catalog_menu_cancel_preserves_all_copies(tmp_path, monkeypatch):
    from tools.launcher.storage import catalog_menu, available
    monkeypatch.delenv("DATABASE_URL", raising=False)
    await prepare(tmp_path)
    first, second = create(tmp_path), create(tmp_path)
    before = {path: path.read_bytes() for path in (first, second)}
    answers = iter(["1", "СКАСУВАТИ"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    catalog_menu(tmp_path)
    assert set(available(tmp_path)) == set(before)
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not (tmp_path / "backups/archive").exists()
