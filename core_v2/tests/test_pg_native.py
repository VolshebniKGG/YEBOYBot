"""Native PostgreSQL orchestration invariants; no server/user data required."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from tools.launcher import pg_native as native

URL = "postgresql+asyncpg://test_operator:private%40percent%25word@db.example:6543/test_database"


def listing(extra=""):
    tables = "\n".join(f"{i}; 1259 {100+i} TABLE public {name} test_operator\n"
                       f"{i+100}; 0 {100+i} TABLE DATA public {name} test_operator"
                       for i, name in enumerate(native.TABLES, 1))
    return (tables + "\n" + extra).encode()


@pytest.fixture
def fake_tools(monkeypatch):
    calls = []
    monkeypatch.setattr(native.shutil, "which", lambda name: "/native tools/" + name)

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, (Path(command[0]).name + " (PostgreSQL) 17.11").encode(), b"")
        if Path(command[0]).name == "pg_dump":
            kwargs["stdout"].write(b"PGDMPnative-test-payload")
            kwargs["stdout"].flush()
            return subprocess.CompletedProcess(command, 0, None, b"")
        if "--list" in command:
            assert kwargs["stdin"].read(5) == b"PGDMP"
            return subprocess.CompletedProcess(command, 0, listing(), b"")
        assert Path(command[0]).name == "pg_restore"
        assert kwargs["stdin"].read(5) == b"PGDMP"
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(native.subprocess, "run", run)
    return calls


def test_connection_credentials_are_only_in_sanitized_child_environment(monkeypatch):
    monkeypatch.setenv("PGOPTIONS", "-c search_path=hostile")
    monkeypatch.setenv("PGSERVICE", "hostile-service")
    monkeypatch.setenv("PGPASSFILE", "/foreign/passwords")
    monkeypatch.setenv("PGPASSWORD", "foreign-password")
    monkeypatch.setenv("PGSSLMODE", "disable")
    monkeypatch.setenv("PGSSLROOTCERT", "/foreign/ca.pem")
    monkeypatch.setenv("HTTPS_PROXY", "https://trusted-proxy.example")
    env = native.connection_env(URL + "?ssl=verify-full")
    assert env["PGPASSWORD"] == "private@percent%word"
    assert env["PGUSER"] == "test_operator"
    assert env["PGDATABASE"] == "test_database"
    assert env["PGHOST"] == "db.example" and env["PGPORT"] == "6543"
    assert env["PGSSLMODE"] == "verify-full"
    assert env["HTTPS_PROXY"] == "https://trusted-proxy.example"
    assert not {"PGOPTIONS", "PGSERVICE", "PGPASSFILE", "PGSSLROOTCERT"} & env.keys()
    assert os.environ["PGPASSWORD"] == "foreign-password"


@pytest.mark.parametrize("url", [
    "sqlite+aiosqlite:///file.db", "postgresql://test:private@host/db", "unparseable-private-url",
    "postgresql+asyncpg://operator:private@/db", "postgresql+asyncpg://operator:private@host/",
    URL + "?options=private-injected-option", URL + "?ssl=unknown-private-mode",
    URL + "?ssl=require&ssl=disable", URL + "?sslrootcert=", URL + "?sslrootcert=bad%0Aprivate",
    URL + "?sslrootcert=%2Fprivate%2Fca.pem", URL + "?sslcert=%2Fprivate%2Fclient.pem",
    URL + "?sslkey=%2Fprivate%2Fkey.pem",
])
def test_unsupported_connection_parameters_fail_without_exposing_values(url, capsys):
    with pytest.raises(native.PostgresError) as error:
        native.connection_env(url)
    assert "private" not in str(error.value)
    assert url not in capsys.readouterr().out


def test_tool_versions_need_matching_sufficient_major(fake_tools, monkeypatch):
    assert native.tool_versions(17) == {"pg_dump": 17, "pg_restore": 17}
    with pytest.raises(native.PostgresError):
        native.tool_versions(18)

    def different(name, args, **kwargs):
        return (name + " (PostgreSQL) " + ("16.5" if name == "pg_restore" else "17.1")).encode()
    monkeypatch.setattr(native, "_run", different)
    with pytest.raises(native.PostgresError, match="однакову"):
        native.tool_versions()


def test_missing_native_tools_does_not_create_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(native.shutil, "which", lambda name: None)
    destination = tmp_path / "snapshot.dump"
    with pytest.raises(native.PostgresError, match="PATH"):
        native.dump(URL, destination, 1)
    assert not destination.exists() and not native.sidecar(destination).exists()


def test_dump_is_private_credential_free_and_exactly_scoped(tmp_path, fake_tools):
    path = tmp_path / "База з пробілами.dump"
    metadata = native.dump(URL, path, 1)
    assert metadata["schema_version"] == 1 and metadata["backend"] == "postgresql"
    assert metadata["tables"] == list(native.TABLES)
    assert native.verify_archive(path) == metadata
    rendered = native.sidecar(path).read_text(encoding="utf-8")
    assert not any(secret in rendered for secret in (URL, "private", "test_operator", "db.example", "test_database"))
    for command, options in fake_tools:
        assert not any(secret in " ".join(command) for secret in (URL, "private", "test_operator", "db.example", "test_database"))
        assert not options.get("shell", False)
        assert options["timeout"] == native.TIMEOUT
    command, options = next((cmd, opts) for cmd, opts in fake_tools if Path(cmd[0]).name == "pg_dump" and "--version" not in cmd)
    assert {arg for arg in command if arg.startswith("--table=")} == {"--table=public." + name for name in native.TABLES}
    assert {"--format=custom", "--no-owner", "--no-acl", "--no-password", "--no-tablespaces"} <= set(command)
    assert options["env"]["PGPASSWORD"] == "private@percent%word"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert native.sidecar(path).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("existing", ["archive", "manifest"])
def test_existing_archive_or_manifest_is_preserved(tmp_path, fake_tools, existing):
    path = tmp_path / "snapshot.dump"
    target = path if existing == "archive" else native.sidecar(path)
    target.write_bytes(b"keep-original-user-data")
    with pytest.raises(native.PostgresError, match="перезапис"):
        native.dump(URL, path, 1)
    assert target.read_bytes() == b"keep-original-user-data"
    assert not any("--format=custom" in command for command, _ in fake_tools)


def test_failed_dump_removes_only_its_own_partial_files(tmp_path, fake_tools, monkeypatch, capsys):
    path = tmp_path / "snapshot.dump"
    original_runner = native.subprocess.run

    def fail(command, **kwargs):
        if "--format=custom" in command:
            kwargs["stdout"].write(b"partial")
            return subprocess.CompletedProcess(command, 1, None, b"private@percent%word sensitive-native-error")
        return original_runner(command, **kwargs)
    monkeypatch.setattr(native.subprocess, "run", fail)
    with pytest.raises(native.PostgresError) as error:
        native.dump(URL, path, 1)
    assert "sensitive-native-error" not in str(error.value)
    assert "private" not in str(error.value)
    assert not path.exists() and not native.sidecar(path).exists()
    assert "private" not in capsys.readouterr().err


def test_dump_timeout_is_bounded_and_sanitized(tmp_path, fake_tools, monkeypatch):
    path = tmp_path / "snapshot.dump"
    original_runner = native.subprocess.run

    def timeout(command, **kwargs):
        if "--format=custom" in command:
            raise subprocess.TimeoutExpired(command, 180, output=b"private-stdout", stderr=b"private-stderr")
        return original_runner(command, **kwargs)
    monkeypatch.setattr(native.subprocess, "run", timeout)
    with pytest.raises(native.PostgresError, match="вчасно") as error:
        native.dump(URL, path, 2)
    assert "private" not in str(error.value)
    assert not path.exists() and not native.sidecar(path).exists()


@pytest.mark.parametrize("change", ["hash", "magic", "manifest_version", "manifest_backend", "manifest_secret", "manifest_date"])
def test_corrupt_archive_or_manifest_is_rejected_before_restore(tmp_path, fake_tools, change):
    path = tmp_path / "snapshot.dump"
    native.dump(URL, path, 1)
    metadata = json.loads(native.sidecar(path).read_text())
    if change == "hash":
        path.write_bytes(path.read_bytes() + b"tamper")
    elif change == "magic":
        path.write_bytes(b"OTHER" + path.read_bytes()[5:])
        metadata["size_bytes"], metadata["sha256"] = native.fingerprint(path)
    elif change == "manifest_version":
        metadata["schema_version"] = 999
    elif change == "manifest_backend":
        metadata["backend"] = "sqlite"
    elif change == "manifest_secret":
        metadata["credentials"] = "private-password-must-not-log"
    elif change == "manifest_date":
        metadata["created_utc"] = "2026-10-26T00:00:00"
    native.sidecar(path).write_text(json.dumps(metadata))
    fake_tools.clear()
    with pytest.raises(native.PostgresError):
        native.restore(URL, path)
    assert not any("--dbname=" in command for command, _ in fake_tools)


@pytest.mark.parametrize("component", ["archive", "manifest"])
def test_symlink_archives_and_manifests_are_refused(tmp_path, fake_tools, component):
    path = tmp_path / "snapshot.dump"
    native.dump(URL, path, 2)
    target = path if component == "archive" else native.sidecar(path)
    saved = tmp_path / "original-user-file"
    target.rename(saved)
    try:
        target.symlink_to(saved)
    except OSError:
        pytest.skip("Symlink creation unavailable to this Windows account")
    with pytest.raises(native.PostgresError, match="посилання"):
        native.verify_archive(path)
    assert saved.exists()


@pytest.mark.parametrize("extra", ["999; 1259 999 TABLE public unrelated_table owner", "999; 1259 999 TABLE other v2_cases owner"])
def test_native_toc_rejects_unexpected_tables(tmp_path, fake_tools, monkeypatch, extra):
    original = native._run
    path = tmp_path / "snapshot.dump"
    native.dump(URL, path, 1)

    def changed(name, args, **kwargs):
        if args == ["--list"]:
            return listing(extra)
        return original(name, args, **kwargs)
    monkeypatch.setattr(native, "_run", changed)
    with pytest.raises(native.PostgresError):
        native.verify_archive(path)


def test_restore_uses_environment_stdin_and_transaction_without_cleaning(tmp_path, fake_tools):
    path = tmp_path / "snapshot.dump"
    data = native.dump(URL, path, 1)
    assert native.restore(URL, path) == data
    command, options = next((cmd, opts) for cmd, opts in fake_tools if "--dbname=" in cmd)
    assert {"--single-transaction", "--exit-on-error", "--no-owner", "--no-acl", "--no-password", "--no-tablespaces"} <= set(command)
    assert not set(command) & {"--clean", "--create", "-C", "-c"}
    assert options["env"]["PGDATABASE"] == "test_database"
    assert options["env"]["PGPASSWORD"] == "private@percent%word"


def test_restore_failure_preserves_archive_and_never_exposes_stderr(tmp_path, fake_tools, monkeypatch):
    path = tmp_path / "snapshot.dump"
    native.dump(URL, path, 1)
    archive, manifest = path.read_bytes(), native.sidecar(path).read_bytes()
    original = native.subprocess.run

    def fail(command, **kwargs):
        if "--dbname=" in command:
            return subprocess.CompletedProcess(command, 1, b"", b"private-server-password")
        return original(command, **kwargs)
    monkeypatch.setattr(native.subprocess, "run", fail)
    with pytest.raises(native.PostgresError) as error:
        native.restore(URL, path)
    assert "private" not in str(error.value)
    assert path.read_bytes() == archive and native.sidecar(path).read_bytes() == manifest


@pytest.mark.parametrize("version", [0, 3, True, "2"])
def test_unsupported_schema_version_never_runs_tools_or_creates_files(tmp_path, fake_tools, version):
    path = tmp_path / "snapshot.dump"
    with pytest.raises(native.PostgresError, match="версія"):
        native.dump(URL, path, version)
    assert fake_tools == [] and not path.exists()


def test_permission_error_is_sanitized_and_keeps_other_files(tmp_path, fake_tools, monkeypatch):
    path = tmp_path / "snapshot.dump"
    sentinel = tmp_path / "keep-user-data.txt"
    sentinel.write_text("keep")
    original_open = native.os.open

    def denied(candidate, flags, mode=0o777, **kwargs):
        if Path(candidate) == path:
            raise PermissionError("private filesystem metadata")
        return original_open(candidate, flags, mode, **kwargs)
    monkeypatch.setattr(native.os, "open", denied)
    with pytest.raises(native.PostgresError) as error:
        native.dump(URL, path, 1)
    assert "private" not in str(error.value)
    assert not path.exists() and not native.sidecar(path).exists()
    assert sentinel.read_text() == "keep"


def test_manifest_creation_race_does_not_remove_foreign_file(tmp_path, fake_tools, monkeypatch):
    path = tmp_path / "snapshot.dump"
    manifest = native.sidecar(path)
    original_open = native.os.open

    def raced(candidate, flags, mode=0o777, **kwargs):
        if Path(candidate) == manifest:
            descriptor = original_open(candidate, flags, mode, **kwargs)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(b"preserve-foreign-manifest")
            raise FileExistsError("another operation created a manifest")
        return original_open(candidate, flags, mode, **kwargs)
    monkeypatch.setattr(native.os, "open", raced)
    with pytest.raises(native.PostgresError):
        native.dump(URL, path, 2)
    assert not path.exists()
    assert manifest.read_bytes() == b"preserve-foreign-manifest"
