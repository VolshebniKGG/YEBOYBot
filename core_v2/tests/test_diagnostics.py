import json
import sqlite3
from types import SimpleNamespace

import pytest

from core_v2.config import Settings
from core_v2.db import Database
from tools.launcher import diagnostics as d
from tools.launcher import process


@pytest.fixture
def configured(monkeypatch, tmp_path):
    settings = SimpleNamespace(token="synthetic-private-token", music=False,
        database="sqlite+aiosqlite:///local.db")
    monkeypatch.setattr(Settings, "load", lambda: settings)
    monkeypatch.setattr(process, "state", lambda root: {"status": "Зупинено", "instance": "private-nonce"})
    monkeypatch.setattr(process, "locked", lambda root: False)
    return settings


@pytest.mark.asyncio
async def test_valid_schema_checked_without_modification(tmp_path, configured):
    path = tmp_path / "local.db"
    db = Database("sqlite+aiosqlite:///" + str(path))
    await db.initialize()
    await db.close()
    before = path.read_bytes()
    report = d.collect(tmp_path)
    assert report["checks"]["database"] == d.result("ok", "sqlite_schema_v2")
    assert report["local_checks_passed"]
    assert not report["runtime"]["discord_ready"]
    assert not report["discord_api_checked"]
    assert path.read_bytes() == before
    encoded = json.dumps(report)
    for secret in (configured.token, configured.database, "private-nonce", str(tmp_path)):
        assert secret not in encoded


def test_missing_database_is_a_failure(tmp_path, configured):
    report = d.collect(tmp_path)
    assert report["checks"]["database"]["code"] == "database_missing"
    assert not report["local_checks_passed"]
    assert not (tmp_path / "local.db").exists()


@pytest.mark.parametrize("version,expected", [(999, "schema_unsupported"), (1, "schema_unsupported")])
def test_incompatible_schema(tmp_path, version, expected):
    path = tmp_path / "db.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE v2_schema(version INTEGER)")
        db.execute("INSERT INTO v2_schema VALUES (?)", (version,))
    assert d.sqlite_check(path)["code"] == expected


def test_corrupt_database(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a database")
    assert d.sqlite_check(path)["code"] == "database_check_failed"
    assert path.read_bytes() == b"not a database"


def test_configuration_failure_never_exports_exception(tmp_path, monkeypatch, configured):
    def fail():
        raise ValueError("synthetic-private-token DATABASE_URL=user:password@host")
    monkeypatch.setattr(Settings, "load", fail)
    report = d.collect(tmp_path)
    assert report["checks"]["configuration"]["code"] == "configuration_invalid"
    assert "password" not in json.dumps(report)
    assert "synthetic-private-token" not in json.dumps(report)


def test_postgresql_is_unknown_without_connecting(tmp_path, configured):
    configured.database = "postgresql+asyncpg://user:private-password@host/db"
    report = d.collect(tmp_path)
    assert report["backend"] == "postgresql"
    assert report["checks"]["database"]["status"] == "unknown"
    assert not report["local_checks_passed"]
    assert "private-password" not in json.dumps(report)


def test_missing_dependency_fails_check(tmp_path, monkeypatch, configured):
    def missing(name):
        raise d.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(d.importlib.metadata, "version", missing)
    report = d.collect(tmp_path)
    assert report["checks"]["dependencies"]["status"] == "error"
    assert all(value is None for value in report["packages"].values())


def test_unhealthy_runtime_does_not_claim_discord_ready(tmp_path, monkeypatch, configured):
    monkeypatch.setattr(process, "locked", lambda root: True)
    monkeypatch.setattr(process, "state", lambda root: {"status": "Не відповідає", "ready": True})
    report = d.collect(tmp_path)
    assert report["runtime"] == {"running": True, "discord_ready": False, "responding": False}


def test_broken_import_is_reported_without_exception_text(tmp_path, monkeypatch, configured):
    def broken(name):
        raise RuntimeError("private-import-path")
    monkeypatch.setattr(d.importlib, "import_module", broken)
    report = d.collect(tmp_path)
    assert report["checks"]["dependencies"]["status"] == "error"
    assert "private-import-path" not in json.dumps(report)
