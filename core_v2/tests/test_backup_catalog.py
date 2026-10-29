"""Safety properties of backup manifests, restore and reversible retention."""
from contextlib import closing
from datetime import datetime
import hashlib
import os
import sqlite3

import pytest

from core_v2.db import Database
from tools.launcher import storage


@pytest.fixture
async def prepared(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    root = (tmp_path / "Бот з пробілами").resolve()
    root.mkdir()
    (root / ".env.v2").write_text(
        "DATABASE_URL=sqlite+aiosqlite:///data/custom bot.db\nDISCORD_TOKEN=synthetic-private-value\n",
        encoding="utf-8")
    database = root / "data/custom bot.db"
    db = Database("sqlite+aiosqlite:///" + str(database))
    await db.initialize()
    await db.configure(123, levels=False)
    await db.close()
    return root, database


def manifest(path):
    return path.with_suffix(path.suffix + ".json")


def change_setting(database):
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE v2_settings SET levels=1")
        connection.commit()


async def test_manifest_matches_bytes_and_excludes_secrets(prepared):
    root, _ = prepared
    saved = storage.create(root)
    info = storage.inspect_backup(saved)
    assert info["status"] == "verified"
    assert info["size_bytes"] == saved.stat().st_size
    assert info["sha256"] == hashlib.sha256(saved.read_bytes()).hexdigest()
    assert datetime.fromisoformat(info["created_utc"].replace("Z", "+00:00")).utcoffset() is not None
    encoded = manifest(saved).read_text(encoding="utf-8")
    for private in ("synthetic-private-value", "DISCORD_TOKEN", "DATABASE_URL", str(root)):
        assert private not in encoded
    assert not list(saved.parent.glob("*.tmp"))


async def test_checksum_mismatch_never_changes_target_or_creates_safety_copy(prepared):
    root, database = prepared
    saved = storage.create(root)
    change_setting(database)
    before = database.read_bytes()
    catalog_before = set(storage.available(root))
    # An otherwise valid SQLite file can contain changed data and pass integrity_check.
    change_setting(saved)
    assert storage.inspect_backup(saved)["status"] == "invalid"
    with pytest.raises(storage.StorageError):
        storage.restore(saved, root=root, confirmed=True)
    assert database.read_bytes() == before
    assert set(storage.available(root)) == catalog_before


async def test_malformed_manifest_is_not_treated_as_legacy(prepared):
    root, database = prepared
    saved = storage.create(root)
    manifest(saved).write_text("{broken", encoding="utf-8")
    assert storage.inspect_backup(saved)["status"] == "invalid"
    before = database.read_bytes()
    with pytest.raises(storage.StorageError):
        storage.restore(saved, root=root, confirmed=True, allow_legacy=True)
    assert database.read_bytes() == before


async def test_legacy_restore_needs_separate_opt_in(prepared):
    root, database = prepared
    saved = storage.create(root)
    manifest(saved).unlink()
    change_setting(database)
    assert storage.inspect_backup(saved)["status"] == "legacy"
    before = database.read_bytes()
    with pytest.raises(storage.StorageError):
        storage.restore(saved, root=root, confirmed=True)
    assert database.read_bytes() == before
    previous = storage.restore(saved, root=root, confirmed=True, allow_legacy=True)
    assert storage.inspect_backup(previous)["status"] == "verified"
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT levels FROM v2_settings WHERE guild_id=123").fetchone() == (0,)


async def test_legacy_corrupt_backup_still_cannot_restore(prepared):
    root, database = prepared
    saved = root / "backups/v2-legacy-invalid.db"
    saved.parent.mkdir(exist_ok=True)
    saved.write_bytes(b"not SQLite")
    before = database.read_bytes()
    with pytest.raises((storage.StorageError, sqlite3.DatabaseError)):
        storage.restore(saved, root=root, confirmed=True, allow_legacy=True)
    assert database.read_bytes() == before


async def test_retention_only_plans_verified_older_copies(prepared):
    root, _ = prepared
    copies = [storage.create(root) for _ in range(3)]
    legacy = root / "backups/v2-0000-legacy.db"
    legacy.write_bytes(copies[0].read_bytes())
    invalid = root / "backups/v2-0001-invalid.db"
    invalid.write_bytes(copies[0].read_bytes())
    manifest(invalid).write_text("{}", encoding="utf-8")
    plan = storage.retention_plan(root, keep=1)
    assert set(plan) == set(copies[:-1])
    assert copies[-1] not in plan
    assert legacy not in plan
    assert invalid not in plan
    assert all(path.exists() for path in copies)


async def test_archive_requires_confirmation_and_keeps_newest(prepared):
    root, _ = prepared
    copies = [storage.create(root) for _ in range(3)]
    plan = storage.retention_plan(root, keep=1)
    with pytest.raises(storage.StorageError):
        storage.archive(plan, root=root, keep=1)
    assert all(path.exists() and manifest(path).exists() for path in copies)
    destinations = storage.archive(plan, root=root, confirmed=True, keep=1)
    assert len(destinations) == 2
    assert copies[-1].exists()
    assert not any(path.exists() for path in copies[:-1])
    assert all(path.is_relative_to(root / "backups/archive") for path in destinations)
    assert all(storage.inspect_backup(path)["status"] == "verified" for path in destinations)
    assert set(storage.available(root)) == {copies[-1], *destinations}
    assert storage.retention_plan(root, keep=1) == []


async def test_archived_backup_can_be_restored(prepared):
    root, database = prepared
    saved = storage.create(root)
    storage.create(root)
    archived = storage.archive([saved], root=root, confirmed=True, keep=1)[0]
    change_setting(database)
    storage.restore(archived, root=root, confirmed=True)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT levels FROM v2_settings WHERE guild_id=123").fetchone() == (0,)


async def test_active_database_inside_backups_is_never_archived(prepared, monkeypatch):
    root, _ = prepared
    copies = [storage.create(root) for _ in range(3)]
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///" + str(copies[0]))
    plan = storage.retention_plan(root, keep=1)
    assert copies[0] not in plan
    with pytest.raises(storage.StorageError):
        storage.archive([copies[0]], root=root, confirmed=True, keep=1)
    assert copies[0].exists() and manifest(copies[0]).exists()


async def test_symlinked_backup_is_not_available(prepared):
    root, _ = prepared
    saved = storage.create(root)
    linked = root / "backups/v2-linked.db"
    try:
        linked.symlink_to(saved)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable for this Windows user")
    assert linked not in storage.available(root)
    with pytest.raises(storage.StorageError):
        storage.restore(linked, root=root, confirmed=True)


async def test_hardlinked_active_database_cannot_restore_onto_itself(prepared):
    root, database = prepared
    linked = root / "backups/v2-hardlink.db"
    linked.parent.mkdir(exist_ok=True)
    try:
        os.link(database, linked)
    except (OSError, NotImplementedError):
        pytest.skip("hardlinks unavailable on this filesystem")
    before = database.read_bytes()
    with pytest.raises(storage.StorageError):
        storage.restore(linked, root=root, confirmed=True, allow_legacy=True)
    assert database.read_bytes() == before


async def test_changed_staged_bytes_are_rejected_before_target_mutation(prepared, monkeypatch):
    root, database = prepared
    saved = storage.create(root)
    change_setting(database)
    before = database.read_bytes()
    catalog_before = set(storage.available(root))
    original_copy = storage.shutil.copyfile

    def corrupt_copy(source, destination, *args, **kwargs):
        result = original_copy(source, destination, *args, **kwargs)
        with open(destination, "ab") as stream:
            stream.write(b"changed-after-selection")
        return result

    monkeypatch.setattr(storage.shutil, "copyfile", corrupt_copy)
    with pytest.raises((storage.StorageError, ValueError)):
        storage.restore(saved, root=root, confirmed=True)
    assert database.read_bytes() == before
    assert set(storage.available(root)) == catalog_before


async def test_archive_manifest_move_failure_rolls_database_back(prepared, monkeypatch):
    from pathlib import Path
    root, _ = prepared
    saved = storage.create(root)
    storage.create(root)
    original_rename = Path.rename

    def fail_manifest_move(path, destination):
        if path == manifest(saved):
            raise OSError("simulated filesystem failure")
        return original_rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_manifest_move)
    with pytest.raises(OSError):
        storage.archive([saved], root=root, confirmed=True, keep=1)
    assert saved.exists() and manifest(saved).exists()
    assert storage.inspect_backup(saved)["status"] == "verified"
    assert saved in storage.available(root)
    assert not list((root / "backups/archive").glob("*/*.db"))


async def test_symlinked_manifest_is_invalid(prepared):
    root, database = prepared
    saved = storage.create(root)
    original = manifest(saved)
    real_metadata = root / "metadata.json"
    original.rename(real_metadata)
    try:
        original.symlink_to(real_metadata)
    except (OSError, NotImplementedError):
        real_metadata.rename(original)
        pytest.skip("symlinks unavailable for this Windows user")
    assert storage.inspect_backup(saved)["status"] == "invalid"
    before = database.read_bytes()
    with pytest.raises(storage.StorageError):
        storage.restore(saved, root=root, confirmed=True, allow_legacy=True)
    assert database.read_bytes() == before


async def test_repeat_inspection_keeps_snapshot_verified_without_wal(prepared):
    root, _ = prepared
    saved = storage.create(root)
    before = saved.read_bytes()
    for _ in range(3):
        assert storage.inspect_backup(saved)["status"] == "verified"
    assert saved.read_bytes() == before
    assert not saved.with_suffix(saved.suffix + "-wal").exists()
    assert not saved.with_suffix(saved.suffix + "-shm").exists()


async def test_manifest_publication_failure_leaves_no_visible_partial_copy(prepared, monkeypatch):
    from pathlib import Path
    root, database = prepared
    before = database.read_bytes()
    original_rename = Path.rename

    def fail_sidecar_publication(path, destination):
        if path.name == "snapshot.db.json":
            raise OSError("simulated manifest publication failure")
        return original_rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_sidecar_publication)
    with pytest.raises(OSError):
        storage.create(root)
    assert storage.available(root) == []
    assert list((root / "backups").iterdir()) == []
    assert database.read_bytes() == before


@pytest.mark.parametrize("key,value", [
    ("format_version", True), ("schema_version", 999), ("backend", "postgresql"),
    ("created_utc", "2026-10-25T12:00:00"), ("size_bytes", -1),
    ("sha256", "not-a-checksum"), ("unexpected_field", "ignored metadata must fail closed"),
])
async def test_invalid_manifest_fields_are_rejected(prepared, key, value):
    import json
    root, _ = prepared
    saved = storage.create(root)
    metadata = json.loads(manifest(saved).read_text(encoding="utf-8"))
    metadata[key] = value
    manifest(saved).write_text(json.dumps(metadata), encoding="utf-8")
    assert storage.inspect_backup(saved)["status"] == "invalid"


@pytest.mark.parametrize("keep", [0, -1, 10001, True, "1"])
async def test_invalid_retention_keep_is_rejected(prepared, keep):
    root, _ = prepared
    with pytest.raises(storage.StorageError):
        storage.retention_plan(root, keep=keep)


async def test_archive_rechecks_stale_plan_before_moving(prepared):
    root, _ = prepared
    saved = storage.create(root)
    newest = storage.create(root)
    selected = storage.retention_plan(root, keep=1)
    assert selected == [saved]
    change_setting(saved)
    with pytest.raises(storage.StorageError):
        storage.archive(selected, root=root, confirmed=True, keep=1)
    assert saved.exists() and newest.exists()
    assert not (root / "backups/archive").exists()


async def test_maintenance_lock_serializes_create_restore_and_archive(prepared):
    root, database = prepared
    saved = storage.create(root)
    storage.create(root)
    before = database.read_bytes()
    with storage.maintenance_lock(root):
        with pytest.raises(RuntimeError):
            storage.create(root)
        with pytest.raises(RuntimeError):
            storage.restore(saved, root=root, confirmed=True)
        with pytest.raises(RuntimeError):
            storage.archive([saved], root=root, confirmed=True, keep=1)
    assert database.read_bytes() == before
    assert storage.inspect_backup(saved)["status"] == "verified"
