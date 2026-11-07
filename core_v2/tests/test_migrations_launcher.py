"""Offline migration safety with real SQLite, backup artifacts and OS locks."""
from contextlib import closing
import hashlib
import json
import sqlite3

import pytest

from core_v2 import migrations as engine
from core_v2.runtime import InstanceLock
from test_migrations_core import create_legacy_v1, payload
from tools.launcher import backup_manifest, diagnostics, migrations as launcher, storage


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    root = (tmp_path / "Бот з пробілами").resolve()
    root.mkdir()
    (root / "data").mkdir()
    path = create_legacy_v1(root / "data/legacy.db")
    (root / ".env.v2").write_text(
        "DATABASE_URL=sqlite+aiosqlite:///data/legacy.db\nDISCORD_TOKEN=synthetic-private-token\n",
        encoding="utf-8")
    return root, path


def version(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        return engine.inspect_sqlite(connection)


def saved_payload(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        return payload(connection)


def report(root):
    return json.loads((root / "data/v2/migration-report.json").read_text(encoding="utf-8"))


def test_plan_reads_actual_version_without_mutation_or_backup(installation):
    root, path = installation
    before = path.read_bytes()
    preview = launcher.plan(root)
    assert preview["current"] == 1 and preview["target"] == 3
    assert preview["revisions"] == ["002_case_updated_at", "003_level_rewards"]
    assert preview["requires_backup"] is True
    assert path.read_bytes() == before
    assert not (root / "backups").exists()
    assert not (root / "data/v2/migration-report.json").exists()
    assert "synthetic-private-token" not in json.dumps(preview)


def test_upgrade_and_recovery_preserve_payload_and_verified_copies(installation):
    root, path = installation
    before = saved_payload(path)
    preview = launcher.plan(root)
    outcome = launcher.apply(root, confirmed=True, expected_plan=preview)
    assert outcome["status"] == "applied"
    assert version(path) == 3
    assert saved_payload(path) == before
    saved = root / outcome["backup"]
    metadata = storage.inspect_backup(saved)
    assert metadata["status"] == "verified" and metadata["schema_version"] == 1
    assert metadata["sha256"] == hashlib.sha256(saved.read_bytes()).hexdigest()
    assert saved_payload(saved) == before
    assert report(root) == outcome
    encoded = json.dumps(outcome)
    assert "synthetic-private-token" not in encoded and "DATABASE_URL" not in encoded
    assert str(root) not in encoded
    # Recovery uses the old snapshot, retaining a safety copy of the migrated DB.
    safety = storage.restore(saved, root=root, confirmed=True)
    assert version(path) == 1 and saved_payload(path) == before
    assert version(safety) == 3 and saved_payload(safety) == before
    assert storage.inspect_backup(safety)["schema_version"] == 3
    assert diagnostics.sqlite_check(path)["code"] == "migration_required"


def test_unconfirmed_upgrade_never_creates_backup_or_changes_db(installation):
    root, path = installation
    before = path.read_bytes()
    with pytest.raises(launcher.MigrationError, match="підтвердження"):
        launcher.apply(root)
    assert path.read_bytes() == before
    assert not (root / "backups").exists()


def test_backup_failure_blocks_ddl_and_preserves_source(installation, monkeypatch):
    root, path = installation
    before = path.read_bytes()
    def fail_backup(root, **kwargs):
        raise storage.StorageError("simulated backup failure")
    monkeypatch.setattr(storage, "_create", fail_backup)
    with pytest.raises(launcher.MigrationError):
        launcher.apply(root, confirmed=True)
    assert path.read_bytes() == before and version(path) == 1
    assert report(root)["status"] == "failed"
    assert report(root)["backup"] is None
    assert not (root / "backups").exists()


@pytest.mark.parametrize("lock_name", ["bot.lock", "backup.lock"])
def test_running_or_maintenance_lock_prevents_changes(installation, lock_name):
    root, path = installation
    before = path.read_bytes()
    with InstanceLock(root / "data/v2" / lock_name):
        with pytest.raises(RuntimeError):
            launcher.apply(root, confirmed=True)
    assert path.read_bytes() == before
    assert not (root / "backups").exists()
    # A failed second lock acquisition must release the first one too.
    with InstanceLock(root / "data/v2/bot.lock"):
        pass


@pytest.mark.parametrize("change", ["config", "file"])
def test_stale_plan_cannot_migrate_reconfigured_or_replaced_source(installation, monkeypatch, change):
    root, path = installation
    preview = launcher.plan(root)
    replacement = create_legacy_v1(root / "data/replacement.db")
    if change == "config":
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///data/replacement.db")
        other_before = path.read_bytes()
        current = replacement
    else:
        replacement.replace(path)
        current = path
        other_before = None
    before = current.read_bytes()
    with pytest.raises(launcher.MigrationError, match="змінився"):
        launcher.apply(root, confirmed=True, expected_plan=preview)
    assert current.read_bytes() == before and version(current) == 1
    if other_before is not None:
        assert path.read_bytes() == other_before
    assert not (root / "backups").exists()


def test_revision_failure_rolls_back_and_keeps_verified_recovery_copy(installation, monkeypatch):
    root, path = installation
    before = path.read_bytes()
    original = engine._apply_revision
    def fail_after_ddl(connection, revision):
        original(connection, revision)
        raise RuntimeError("synthetic-private-error")
    monkeypatch.setattr(engine, "_apply_revision", fail_after_ddl)
    with pytest.raises(launcher.MigrationError) as failure:
        launcher.apply(root, confirmed=True)
    assert "synthetic-private-error" not in str(failure.value)
    assert path.read_bytes() == before and version(path) == 1
    outcome = report(root)
    assert outcome["status"] == "failed" and outcome["error_type"] == "RuntimeError"
    saved = root / outcome["backup"]
    assert storage.inspect_backup(saved)["status"] == "verified"
    assert version(saved) == 1
    assert "synthetic-private-error" not in json.dumps(outcome)


def test_repeated_latest_upgrade_is_noop_without_extra_backup(installation):
    root, path = installation
    launcher.apply(root, confirmed=True)
    copies = storage.available(root)
    before = path.read_bytes()
    repeated = launcher.apply(root, confirmed=True, expected_plan=launcher.plan(root))
    assert repeated["status"] == "unchanged" and repeated["revisions"] == []
    assert repeated["backup"] is None
    assert storage.available(root) == copies
    assert path.read_bytes() == before


def test_menu_cancel_leaves_source_and_catalog_unchanged(installation, monkeypatch):
    root, path = installation
    before = path.read_bytes()
    monkeypatch.setattr("builtins.input", lambda prompt: "СКАСУВАТИ")
    launcher.menu(root)
    assert path.read_bytes() == before
    assert not (root / "backups").exists()


def test_report_failure_before_ddl_does_not_change_source(installation, monkeypatch):
    root, path = installation
    before = path.read_bytes()
    def deny_report(*args):
        raise OSError("synthetic report failure")
    monkeypatch.setattr(launcher, "write_report", deny_report)
    with pytest.raises(launcher.MigrationError):
        launcher.apply(root, confirmed=True)
    assert path.read_bytes() == before and version(path) == 1
    copies = storage.available(root)
    assert len(copies) == 1 and storage.inspect_backup(copies[0])["status"] == "verified"


def test_report_failure_after_commit_reports_actual_applied_database(installation, monkeypatch):
    root, path = installation
    before = saved_payload(path)
    original = launcher.write_report
    def fail_applied_report(root, outcome):
        if outcome["status"] == "applied":
            raise OSError("synthetic report failure")
        original(root, outcome)
    monkeypatch.setattr(launcher, "write_report", fail_applied_report)
    with pytest.raises(launcher.MigrationError, match="БД оновлено"):
        launcher.apply(root, confirmed=True)
    assert version(path) == 3 and saved_payload(path) == before
    saved = root / report(root)["backup"]
    assert storage.inspect_backup(saved)["status"] == "verified" and version(saved) == 1


def test_corrupted_recovery_copy_prevents_migration(installation, monkeypatch):
    root, path = installation
    before = path.read_bytes()
    create = storage._create
    corrupted = []
    def corrupt_after_backup(root, **kwargs):
        saved = create(root, **kwargs)
        saved.write_bytes(b"not a database")
        corrupted.append(saved)
        return saved
    monkeypatch.setattr(storage, "_create", corrupt_after_backup)
    with pytest.raises(launcher.MigrationError):
        launcher.apply(root, confirmed=True)
    assert path.read_bytes() == before and version(path) == 1
    assert storage.inspect_backup(corrupted[0])["status"] == "invalid"
    assert report(root)["status"] == "failed"


def test_diagnostics_transition_from_required_to_current(installation):
    root, path = installation
    assert diagnostics.sqlite_check(path) == diagnostics.result("error", "migration_required")
    launcher.apply(root, confirmed=True)
    assert diagnostics.sqlite_check(path) == diagnostics.result("ok", "sqlite_schema_v3")


def test_manifest_schema_mismatch_is_invalid_even_with_correct_hash(installation):
    root, _ = installation
    saved = storage.create(root)
    metadata_path = backup_manifest.sidecar(saved)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["schema_version"] = 2
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert storage.inspect_backup(saved)["status"] == "invalid"


def test_postgresql_upgrade_is_explicitly_blocked_without_touching_sqlite(installation, monkeypatch):
    root, path = installation
    before = path.read_bytes()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test_only@localhost/yeboy_test")
    with pytest.raises(launcher.MigrationError, match="PostgreSQL"):
        launcher.apply(root, confirmed=True)
    assert path.read_bytes() == before
    assert not (root / "backups").exists()


def test_unknown_schema_fails_before_backup_or_ddl(installation):
    root, path = installation
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE v2_schema SET version=999")
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(launcher.MigrationError):
        launcher.apply(root, confirmed=True)
    assert path.read_bytes() == before
    assert not (root / "backups").exists()


def test_configuration_change_during_backup_cannot_protect_wrong_database(installation, monkeypatch):
    root, path = installation
    other = create_legacy_v1(root / "data/other.db")
    before, other_before = path.read_bytes(), other.read_bytes()
    create = storage._create
    def reconfigure_during_backup(root, **kwargs):
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///data/other.db")
        return create(root, **kwargs)
    monkeypatch.setattr(storage, "_create", reconfigure_during_backup)
    with pytest.raises(launcher.MigrationError):
        launcher.apply(root, confirmed=True)
    assert path.read_bytes() == before and other.read_bytes() == other_before
    assert version(path) == version(other) == 1
    saved = root / report(root)["backup"]
    assert saved_payload(saved) == saved_payload(path)
    assert storage.inspect_backup(saved)["status"] == "verified"
