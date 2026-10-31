"""Legacy XP imports use synthetic JSON, real SQLite, backups and OS locks."""
from contextlib import closing
import json
import re
import sqlite3

import pytest
from sqlalchemy import create_engine

from core_v2.db import CURRENT_SCHEMA_VERSION, meta
from core_v2.runtime import InstanceLock
from tools.launcher import rank_import as importer, storage


def records(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT * FROM v2_xp ORDER BY guild_id, user_id").fetchall()


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    root = tmp_path / "Бот з пробілами"
    path = root / "custom folder/bot.db"
    path.parent.mkdir(parents=True)
    (root / ".env.v2").write_text(
        "DATABASE_URL=sqlite+aiosqlite:///custom folder/bot.db\nDISCORD_TOKEN=synthetic-private-token\n",
        encoding="utf-8")
    engine = create_engine("sqlite:///" + str(path))
    try:
        meta.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO v2_schema VALUES (?)", (CURRENT_SCHEMA_VERSION,))
            connection.exec_driver_sql("INSERT INTO v2_xp VALUES (11,101,30,100,123,456)")
            connection.exec_driver_sql("INSERT INTO v2_xp VALUES (22,101,999,888,12,34)")
            connection.exec_driver_sql("INSERT INTO v2_xp VALUES (11,777,5,5,55,66)")
    finally:
        engine.dispose()
    source = root / "data/rank/11/levels.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({
        "101": {"xp_text": 50, "xp_voice": 60, "level_text": 999, "level_voice": -4},
        "202": {"xp": 345, "level": "ignored"},
        "303": {"xp_voice": 70},
    }), encoding="utf-8")
    return root, path, source


def test_plan_validates_without_writes_or_private_rows(installation):
    root, path, source = installation
    original_db, original_json = path.read_bytes(), source.read_bytes()
    preview = importer.plan("11", root)
    assert (preview["guild_id"], preview["row_count"], preview["policy"]) == (11, 3, "max_per_counter")
    assert path.read_bytes() == original_db and source.read_bytes() == original_json
    assert not (root / "backups").exists()
    assert not (root / "data/v2").exists()
    encoded = json.dumps(preview)
    assert "synthetic-private-token" not in encoded
    assert "xp_text" not in encoded and "users" not in preview and "rows" not in preview


def test_merge_is_max_per_counter_guild_scoped_and_replayable(installation):
    root, path, source = installation
    before = records(path)
    source_bytes = source.read_bytes()
    preview = importer.plan(11, root)
    outcome = importer.apply(11, root, confirmed=True, expected_plan=preview)
    expected = [(11, 101, 50, 100, 123.0, 456.0),
                (11, 202, 345, 0, 0.0, 0.0),
                (11, 303, 0, 70, 0.0, 0.0),
                (11, 777, 5, 5, 55.0, 66.0),
                (22, 101, 999, 888, 12.0, 34.0)]
    assert records(path) == expected
    assert outcome["guild_id"] == 11 and outcome["row_count"] == 3
    saved = root / outcome["backup"]
    assert storage.inspect_backup(saved)["status"] == "verified"
    assert records(saved) == before
    importer.apply(11, root, confirmed=True)
    assert records(path) == expected
    assert source.read_bytes() == source_bytes
    assert "synthetic-private-token" not in json.dumps(outcome)


@pytest.mark.parametrize("raw", [
    "[]", '{"101": []}', '{"101": {}}', '{"101": {"level": 2}}',
    '{"101": {"xp_text": -1}}', '{"101": {"xp_voice": 1.5}}',
    '{"101": {"xp": true}}', '{"101": {"xp_text": "40"}}',
    '{"101": {"xp": null}}', '{"101": {"xp_text": 9223372036854775808}}',
    '{"101": {"xp_text": 1, "xp": 2}}',
    '{"0": {"xp": 1}}', '{"-1": {"xp": 1}}', '{"01": {"xp": 1}}',
    '{"１２": {"xp": 1}}', '{"9223372036854775808": {"xp": 1}}',
    '{"101": {"xp": 1}, "202": {"xp_voice": false}}',
    '{"101": {"xp": 1}, "101": {"xp": 2}}',
    '{"101": {"xp_text": 1, "xp_text": 2}}',
    '{"101": {"xp": 1, "level": {"x": 1, "x": 2}}}',
    '{"101": {"xp": 1, "level": NaN}}',
    '{"101": {"xp": 1, "level": Infinity}}',
    '{"101": {"xp": 1, "level": 1e999}}',
    '{"101": {"xp": 1}} trailing', "{", "\ud800",
])
def test_any_invalid_row_or_json_blocks_entire_import(installation, raw):
    root, path, source = installation
    source.write_bytes(raw.encode("utf-8", errors="surrogatepass"))
    before = path.read_bytes()
    with pytest.raises(importer.RankImportError):
        importer.apply(11, root, confirmed=True)
    assert path.read_bytes() == before
    assert not (root / "backups").exists()
    assert not (root / "data/v2").exists()


@pytest.mark.parametrize("guild_id", [True, False, 0, -1, 2**63, "0", "01", "１１", "11/../22", "+11", "11 "])
def test_invalid_guild_scope_cannot_choose_other_sources(installation, guild_id):
    root, path, _ = installation
    before = path.read_bytes()
    with pytest.raises(importer.RankImportError):
        importer.apply(guild_id, root, confirmed=True)
    assert path.read_bytes() == before and not (root / "backups").exists()


def test_signed_integer_boundary_and_legacy_counter_alias(installation):
    root, path, source = installation
    source.write_text(json.dumps({str(2**63 - 1): {
        "xp": 2**63 - 1, "xp_text": 2**63 - 1, "xp_voice": 0,
        "level_text": {"ignored": [1, 2]},
    }}), encoding="utf-8")
    importer.apply(11, root, confirmed=True)
    assert (11, 2**63 - 1, 2**63 - 1, 0, 0.0, 0.0) in records(path)


def test_source_byte_and_row_bounds(installation, monkeypatch):
    root, path, source = installation
    before = path.read_bytes()
    source.write_bytes(b" " * (importer.MAX_SOURCE_BYTES + 1))
    with pytest.raises(importer.RankImportError, match="10 MiB"):
        importer.apply(11, root, confirmed=True)
    # Exercise the row bound with the real JSON path while keeping this test small.
    monkeypatch.setattr(importer, "MAX_ROWS", 1)
    source.write_text('{"101":{"xp":1},"202":{"xp":2}}', encoding="utf-8")
    with pytest.raises(importer.RankImportError, match="50000"):
        importer.apply(11, root, confirmed=True)
    assert path.read_bytes() == before and not (root / "backups").exists()


def test_confirmation_is_required_before_any_work(installation):
    root, path, _ = installation
    before = path.read_bytes()
    with pytest.raises(importer.RankImportError, match="підтвердження"):
        importer.apply(11, root)
    assert path.read_bytes() == before and not (root / "backups").exists()
    assert not (root / "data/v2").exists()


@pytest.mark.parametrize("lock_name", ["bot.lock", "backup.lock"])
def test_running_bot_or_maintenance_lock_blocks_import(installation, lock_name):
    root, path, _ = installation
    before = path.read_bytes()
    with InstanceLock(root / "data/v2" / lock_name):
        with pytest.raises(importer.RankImportError):
            importer.apply(11, root, confirmed=True)
    assert path.read_bytes() == before and not (root / "backups").exists()
    with InstanceLock(root / "data/v2/bot.lock"):
        pass


def test_backup_failure_prevents_any_sql_changes(installation, monkeypatch):
    root, path, _ = installation
    before = path.read_bytes()
    def fail(*_args, **_kwargs):
        raise storage.StorageError("synthetic-private-backup-error")
    monkeypatch.setattr(storage, "_create", fail)
    with pytest.raises(importer.RankImportError) as failure:
        importer.apply(11, root, confirmed=True)
    assert "synthetic-private" not in str(failure.value)
    assert path.read_bytes() == before and not (root / "backups").exists()


def test_sql_failure_rolls_back_all_rows_and_keeps_recovery_copy(installation, monkeypatch):
    root, path, source = installation
    original_db, original_json, before = path.read_bytes(), source.read_bytes(), records(path)
    merge = importer._merge
    def fail_after_merge(connection, rows):
        assert connection.in_transaction
        merge(connection, rows)
        raise sqlite3.OperationalError("synthetic-private-row-error")
    monkeypatch.setattr(importer, "_merge", fail_after_merge)
    with pytest.raises(importer.RankImportError) as failure:
        importer.apply(11, root, confirmed=True)
    assert "synthetic-private" not in str(failure.value)
    assert records(path) == before and path.read_bytes() == original_db
    assert source.read_bytes() == original_json
    copies = storage.available(root)
    assert len(copies) == 1 and storage.inspect_backup(copies[0])["status"] == "verified"
    assert records(copies[0]) == before


@pytest.mark.parametrize("change", ["source", "replacement", "config"])
def test_stale_preview_requires_fresh_confirmation(installation, monkeypatch, change):
    root, path, source = installation
    preview = importer.plan(11, root)
    if change == "source":
        source.write_text('{"101":{"xp":500}}', encoding="utf-8")
    elif change == "replacement":
        replacement = source.with_suffix(".new")
        replacement.write_bytes(source.read_bytes())
        replacement.replace(source)
    else:
        import shutil
        other = path.with_name("other.db")
        shutil.copyfile(path, other)
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///custom folder/other.db")
    before = path.read_bytes()
    with pytest.raises(importer.RankImportError, match="змінився"):
        importer.apply(11, root, confirmed=True, expected_plan=preview)
    assert path.read_bytes() == before and not (root / "backups").exists()


@pytest.mark.parametrize("stage", ["backup", "merge"])
def test_source_change_during_import_aborts_or_rolls_back(installation, monkeypatch, stage):
    root, path, source = installation
    before = path.read_bytes()
    if stage == "backup":
        create = storage._create
        def changed_backup(*args, **kwargs):
            saved = create(*args, **kwargs)
            source.write_text('{"101":{"xp":500}}', encoding="utf-8")
            return saved
        monkeypatch.setattr(storage, "_create", changed_backup)
    else:
        merge = importer._merge
        def changed_merge(connection, rows):
            merge(connection, rows)
            source.write_text('{"101":{"xp":500}}', encoding="utf-8")
        monkeypatch.setattr(importer, "_merge", changed_merge)
    with pytest.raises(importer.RankImportError, match="змінився"):
        importer.apply(11, root, confirmed=True)
    assert path.read_bytes() == before
    assert len(storage.available(root)) == 1


def test_verified_backup_precedes_begin_and_schema_rechecked_under_sql_lock(installation, monkeypatch):
    root, path, _ = installation
    before = path.read_bytes()
    create = storage._create
    checkpoints = []
    def backup_without_writer(*args, **kwargs):
        with closing(sqlite3.connect(path, timeout=0)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        saved = create(*args, **kwargs)
        checkpoints.append("backup")
        return saved
    def changed_schema(cursor):
        assert cursor.connection.in_transaction
        assert checkpoints == ["backup"]
        checkpoints.append("schema")
        return 1
    monkeypatch.setattr(storage, "_create", backup_without_writer)
    monkeypatch.setattr(importer, "sqlite_version", changed_schema)
    with pytest.raises(importer.RankImportError, match="Схема"):
        importer.apply(11, root, confirmed=True)
    assert checkpoints == ["backup", "schema"] and path.read_bytes() == before


@pytest.mark.parametrize("schema", ["v1", "future", "malformed", "missing"])
def test_existing_current_schema_is_required_without_migration(installation, schema):
    root, path, _ = installation
    if schema == "missing":
        path.unlink()
    else:
        with closing(sqlite3.connect(path)) as connection:
            if schema == "v1":
                connection.execute("DROP TABLE v2_level_rewards")
                connection.execute("ALTER TABLE v2_cases DROP COLUMN updated")
                connection.execute("UPDATE v2_schema SET version=1")
            elif schema == "future":
                connection.execute("UPDATE v2_schema SET version=999")
            else:
                connection.execute("DROP TABLE v2_xp")
            connection.commit()
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(importer.RankImportError) as failure:
        importer.apply(11, root, confirmed=True)
    if schema == "v1":
        assert "[21]" in str(failure.value)
    assert (path.read_bytes() if path.exists() else None) == before
    assert not (root / "backups").exists() and not (root / "data/v2").exists()


def test_postgres_requires_separate_workflow(installation, monkeypatch):
    root, path, _ = installation
    before = path.read_bytes()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:synthetic-private@localhost/bot")
    with pytest.raises(importer.RankImportError, match="PostgreSQL") as failure:
        importer.apply(11, root, confirmed=True)
    assert "synthetic-private" not in str(failure.value)
    assert path.read_bytes() == before and not (root / "backups").exists()


def test_invalid_database_url_does_not_disclose_configuration(installation, monkeypatch):
    root, path, _ = installation
    before = path.read_bytes()
    monkeypatch.setenv("DATABASE_URL", "synthetic-private-invalid-url")
    with pytest.raises(importer.RankImportError) as failure:
        importer.apply(11, root, confirmed=True)
    assert "synthetic-private" not in str(failure.value)
    assert path.read_bytes() == before and not (root / "backups").exists()


@pytest.mark.parametrize("change", ["config", "schema"])
def test_database_change_during_merge_rolls_back(installation, monkeypatch, change):
    root, path, _ = installation
    before = path.read_bytes()
    merge = importer._merge
    def changed_database(connection, rows):
        merge(connection, rows)
        if change == "config":
            monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///other.db")
        else:
            connection.execute("UPDATE v2_schema SET version=1")
    monkeypatch.setattr(importer, "_merge", changed_database)
    with pytest.raises(importer.RankImportError):
        importer.apply(11, root, confirmed=True)
    assert path.read_bytes() == before
    assert len(storage.available(root)) == 1


@pytest.mark.parametrize("answer", ["СКАСУВАТИ", "ІМПОРТУВАТИ XP"])
def test_menu_previews_only_guild_count_policy_and_requires_exact_phrase(installation, monkeypatch, capsys, answer):
    root, path, source = installation
    before = path.read_bytes()
    answers = iter(["11", answer])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    importer.menu(root)
    output = capsys.readouterr().out
    assert "Сервер: 11 | Користувачів: 3" in output and "max(" in output
    assert not re.search(r"\b(?:101|202|345)\b", output)
    assert source.exists()
    if answer == "СКАСУВАТИ":
        assert path.read_bytes() == before and not (root / "backups").exists()
    else:
        assert len(records(path)) == 5 and len(storage.available(root)) == 1
