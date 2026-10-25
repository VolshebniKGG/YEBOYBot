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
