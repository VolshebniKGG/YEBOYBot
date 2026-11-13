"""Bootstrap failure diagnostics and offline creation with hostile parent settings."""
import errno
import json
import subprocess
import sys
from unittest.mock import Mock

import pytest

from tools.launcher import bootstrap as b


PRIVATE = "secret-token https://user:password@private.invalid/ C:\\Users\\Private"


def os_failure(code, windows_code):
    error = OSError(code, PRIVATE)
    # A Windows error can be exercised on Linux without changing os.name or
    # relying on the machine's localized strerror translation.
    if windows_code is not None:
        error.winerror = windows_code
    return error


FAILURES = [
    (errno.EACCES, None, "access-denied"),
    (errno.EACCES, 5, "access-denied"),
    (errno.EACCES, 1260, "execution-policy"),
    (errno.EACCES, 577, "execution-policy"),
    (errno.EACCES, 4551, "execution-policy"),
    (errno.EACCES, 225, "security-blocked"),
    (errno.EACCES, 226, "security-blocked"),
    (errno.ENOSPC, None, "disk-full"),
    (errno.ENOSPC, 112, "disk-full"),
    (errno.ENOENT, None, "missing-file"),
    (errno.ENOENT, 2, "missing-file"),
    (errno.ENOENT, 3, "missing-file"),
    (errno.ENAMETOOLONG, None, "path-too-long"),
    (errno.ENAMETOOLONG, 206, "path-too-long"),
]


@pytest.mark.parametrize("code,windows_code,category", FAILURES)
def test_create_error_preserves_os_codes_without_disclosing_paths(
        tmp_path, monkeypatch, code, windows_code, category):
    monkeypatch.setattr(b.venv.EnvBuilder, "create", Mock(side_effect=os_failure(code, windows_code)))
    with pytest.raises(b.BootstrapError) as caught:
        b.create_environment(tmp_path / ".venv-v2")
    failure = caught.value
    assert failure.stage == "venv-create"
    assert failure.category == category
    assert failure.errno == code
    assert failure.winerror == windows_code
    assert failure.exit_code is None
    b.write_failure_report(tmp_path, failure)
    content = (tmp_path / "data/v2/install-report.json").read_text(encoding="utf-8")
    report = json.loads(content)
    assert report["errno"] == code
    assert report["winerror"] == windows_code
    for secret in ("secret-token", "password", "private.invalid", "Users\\Private"):
        assert secret not in str(failure)
        assert secret not in content


@pytest.mark.parametrize("arguments,stage", [
    (["-I", "-c", b.ENVIRONMENT_PROBE], "environment-probe"),
    (["-I", "-m", "ensurepip", "--upgrade"], "ensurepip"),
])
def test_child_launch_denial_names_actual_stage(tmp_path, monkeypatch, arguments, stage):
    monkeypatch.setattr(b.subprocess, "run", Mock(side_effect=os_failure(errno.EACCES, 5)))
    with pytest.raises(b.BootstrapError) as caught:
        b.run([str(tmp_path / "python.exe"), *arguments], tmp_path)
    assert caught.value.stage == stage
    assert caught.value.category == "access-denied"
    assert caught.value.winerror == 5
    assert caught.value.errno == errno.EACCES
    assert PRIVATE not in str(caught.value)


def test_ensurepip_timeout_is_not_reported_as_creation_or_missing_module(tmp_path, monkeypatch):
    timeout = subprocess.TimeoutExpired([PRIVATE], 600, output=PRIVATE, stderr=PRIVATE)
    monkeypatch.setattr(b.subprocess, "run", Mock(side_effect=timeout))
    with pytest.raises(b.BootstrapError) as caught:
        b.run(["python", "-I", "-m", "ensurepip", "--upgrade"], tmp_path)
    assert caught.value.stage == "ensurepip"
    assert caught.value.category == "timeout"
    assert "secret-token" not in str(caught.value)


def test_unrecognized_process_exit_keeps_exit_code_without_inventing_cause(tmp_path, monkeypatch):
    monkeypatch.setattr(b.subprocess, "run", Mock(return_value=subprocess.CompletedProcess(
        [PRIVATE], 91, PRIVATE, PRIVATE)))
    with pytest.raises(b.BootstrapError) as caught:
        b.run(["python", "-I", "-m", "ensurepip", "--upgrade"], tmp_path)
    assert caught.value.stage == "ensurepip"
    assert caught.value.category == "unknown"
    assert caught.value.exit_code == 91
    assert "secret-token" not in str(caught.value)


@pytest.mark.parametrize("category", [
    "access-denied", "execution-policy", "security-blocked", "disk-full", "timeout", "unknown",
])
def test_pip_probe_failure_does_not_trigger_blind_reinstallation(tmp_path, monkeypatch, category):
    stamp = tmp_path / "stamp"
    failure = b.BootstrapError("Safe diagnostic", stage="pip-version", category=category)
    runner = Mock(side_effect=failure)
    monkeypatch.setattr(b, "run", runner)
    with pytest.raises(b.BootstrapError) as caught:
        b.ensure_pip(tmp_path, stamp)
    assert caught.value is failure
    assert runner.call_count == 1


def test_missing_pip_uses_local_bundle_in_target_environment(tmp_path, monkeypatch):
    calls = []
    def run(args, root):
        calls.append(args)
        assert root == tmp_path
        assert args[0] == str(b.python_path(tmp_path))
        assert args[1] == "-I"
        if len(calls) == 1:
            raise b.BootstrapError("Missing pip", stage="pip-version", category="missing-module")
        if "ensurepip" in args:
            return ""
        assert b.PIP_VERSION_CHECK in args
        return b.PIP_VERSION
    monkeypatch.setattr(b, "run", run)
    b.ensure_pip(tmp_path, tmp_path / "stamp")
    assert len(calls) == 3
    assert "ensurepip" in calls[1]
    assert not any("install" in args for args in calls)


def test_importlib_package_not_found_is_recognized_as_missing_pip():
    category, hint = b.failure_hint(
        "importlib.metadata.PackageNotFoundError: No package metadata was found for pip\n" + PRIVATE)
    assert category == "missing-module"
    assert "secret-token" not in hint


@pytest.mark.parametrize("category", [
    "access-denied", "file-locked", "execution-policy", "security-blocked",
    "disk-full", "timeout", "unknown", "missing-module", "probe-syntax",
])
def test_known_external_failure_does_not_move_owned_environment(tmp_path, monkeypatch, category):
    root = tmp_path.resolve()
    (root / "core_v2").mkdir()
    (root / "core_v2/requirements.txt").write_text("unused==1\n", encoding="utf-8")
    (root / ".env.v2").write_text("DISCORD_TOKEN=private-value", encoding="utf-8")
    interpreter = b.python_path(root)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"owned interpreter placeholder")
    owner = root / ".venv-v2/.yeboy-core-owner"
    owner.write_text(str(root), encoding="utf-8")
    sentinel = root / ".venv-v2/keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    failure = b.BootstrapError("Safe diagnostic", stage="environment-probe", category=category)
    monkeypatch.setattr(b, "validate_environment", Mock(side_effect=failure))
    monkeypatch.setattr(b, "create_environment", Mock(side_effect=AssertionError("Unnecessary rebuild")))
    monkeypatch.setattr(b, "ensure_pip", Mock(side_effect=AssertionError("Package changes after failure")))
    with pytest.raises(b.BootstrapError) as caught:
        b.prepare(root)
    assert caught.value is failure
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert owner.read_text(encoding="utf-8") == str(root)
    assert (root / ".env.v2").read_text(encoding="utf-8") == "DISCORD_TOKEN=private-value"
    assert list(root.glob(".venv-v2.failed-*")) == []
    assert not (root / ".venv-v2/yeboy-requirements.sha256").exists()


def test_only_explicit_broken_interpreter_attempts_one_owned_rebuild(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / "core_v2").mkdir()
    (root / "core_v2/requirements.txt").write_text("unused==1\n", encoding="utf-8")
    interpreter = b.python_path(root)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"stale but owned python placeholder")
    (root / ".venv-v2/.yeboy-core-owner").write_text(str(root), encoding="utf-8")
    sentinel = root / ".venv-v2/keep.txt"
    sentinel.write_text("untouched backup", encoding="utf-8")
    broken = b.BootstrapError("Known broken interpreter", stage="environment-probe",
                              category="broken-interpreter")
    denied = b.BootstrapError("Access denied", stage="environment-probe",
                              category="access-denied", errno=13, winerror=5)
    validator = Mock(side_effect=[broken, denied])
    creator = Mock()
    monkeypatch.setattr(b, "validate_environment", validator)
    monkeypatch.setattr(b, "create_environment", creator)
    monkeypatch.setattr(b, "ensure_pip", Mock(side_effect=AssertionError("Pip must not be started")))
    with pytest.raises(b.BootstrapError) as caught:
        b.prepare(root)
    assert caught.value is denied
    assert validator.call_count == 2
    creator.assert_called_once()
    backups = list(root.glob(".venv-v2.failed-*"))
    assert len(backups) == 1
    assert (backups[0] / "keep.txt").read_text(encoding="utf-8") == "untouched backup"
    assert not (root / ".venv-v2/yeboy-requirements.sha256").exists()


@pytest.mark.skipif(sys.version_info[:2] != (3, 12), reason="Supported bootstrap Python 3.12 required")
def test_real_creation_and_bundled_pip_ignore_hostile_parent_environment(tmp_path, monkeypatch):
    # Offline integration: ensurepip uses the wheel bundled with CPython;
    # deliberately do not call ensure_pip(), which would upgrade pip online.
    root = tmp_path / "Бот з пробілами"
    root.mkdir()
    foreign = tmp_path / "foreign-packages"
    for key, value in {
        "PYTHONHOME": str(tmp_path / "missing-python"),
        "PYTHONPATH": str(foreign),
        "PIP_TARGET": str(foreign),
        "PIP_PREFIX": str(tmp_path / "foreign-prefix"),
        "VIRTUAL_ENV": str(tmp_path / "foreign-env"),
        "PIP_CONFIG_FILE": str(tmp_path / "untrusted-pip.ini"),
    }.items():
        monkeypatch.setenv(key, value)
    b.create_environment(root / ".venv-v2")
    b.validate_environment(root)
    interpreter = str(b.python_path(root))
    with pytest.raises(b.BootstrapError) as missing:
        b.run([interpreter, "-I", "-c", b.PIP_VERSION_CHECK], root)
    assert missing.value.category == "missing-module"
    b.run([interpreter, "-I", "-m", "ensurepip", "--upgrade"], root)
    assert b.run([interpreter, "-I", "-c", b.PIP_VERSION_CHECK], root).strip()
    b.validate_environment(root)
    assert not foreign.exists()
    assert not (tmp_path / "foreign-prefix").exists()
    assert not (root / ".venv-v2/yeboy-requirements.sha256").exists()
