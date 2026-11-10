"""Diagnostic regressions: preserve evidence and never expose raw exceptions."""
import errno
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools.launcher import bootstrap as b


PRIVATE = "private-token https://user:password@private.invalid/ C:\\Users\\Private"


def windows_error(code, windows_code):
    error = OSError(code, PRIVATE)
    error.winerror = windows_code
    return error


@pytest.mark.parametrize("windows_code,category", [
    (5, "access-denied"),
    (32, "file-locked"),
    (33, "file-locked"),
    (112, "disk-full"),
    (206, "path-too-long"),
    (577, "execution-policy"),
    (1260, "execution-policy"),
    (4551, "execution-policy"),
])
def test_specific_windows_code_takes_priority_over_generic_errno(windows_code, category):
    result, hint = b.os_failure_hint(windows_error(errno.EACCES, windows_code))
    assert result == category
    assert PRIVATE not in hint


@pytest.mark.parametrize("windows_code,category", [
    (5, "access-denied"),
    (32, "file-locked"),
    (33, "file-locked"),
    (577, "execution-policy"),
    (1260, "execution-policy"),
    (225, "security-blocked"),
    (112, "disk-full"),
    (206, "path-too-long"),
])
def test_localized_subprocess_error_uses_complete_numeric_code(windows_code, category):
    result, hint = b.failure_hint(f"OSError: [WinError {windows_code}] Локалізований текст {PRIVATE}")
    assert result == category
    assert "private-token" not in hint
    assert "private.invalid" not in hint


@pytest.mark.parametrize("code", [50, 500, 5000, 5770])
def test_unknown_windows_code_is_not_truncated_to_winerror_five(code):
    category, _ = b.failure_hint(f"[WinError {code}] unknown detail")
    assert category != "access-denied"


def test_configuration_read_denial_keeps_real_evidence(tmp_path, monkeypatch):
    target = tmp_path / ".venv-v2"
    target.mkdir()
    cfg = target / "pyvenv.cfg"
    cfg.write_text("include-system-site-packages = false\n", encoding="utf-8")
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == cfg:
            raise windows_error(errno.EACCES, 5)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    runner = Mock(side_effect=AssertionError("Must stop before executing Python"))
    monkeypatch.setattr(b, "run", runner)
    with pytest.raises(b.BootstrapError) as caught:
        b.validate_environment(tmp_path)
    assert caught.value.stage == "environment-validation"
    assert caught.value.category == "access-denied"
    assert caught.value.winerror == 5
    assert PRIVATE not in str(caught.value)
    runner.assert_not_called()


def test_invalid_configuration_is_distinct_from_execution_failure(tmp_path):
    target = tmp_path / ".venv-v2"
    target.mkdir()
    (target / "pyvenv.cfg").write_text("include-system-site-packages = true\n", encoding="utf-8")
    with pytest.raises(b.BootstrapError) as caught:
        b.validate_environment(tmp_path)
    assert caught.value.stage == "environment-validation"
    assert caught.value.category == "invalid-environment"


@pytest.mark.parametrize("error", [
    OSError(errno.ENOSPC, PRIVATE),
    RuntimeError(PRIVATE),
])
def test_main_never_prints_or_records_untrusted_exception_text(tmp_path, monkeypatch, capsys, error):
    monkeypatch.setattr(b, "ROOT", tmp_path)
    monkeypatch.setattr(b, "prepare", Mock(side_effect=error))
    assert b.main() == 1
    console = capsys.readouterr().out
    report = (tmp_path / "data/v2/install-report.json").read_text(encoding="utf-8")
    for value in ("private-token", "private.invalid", "password", "Users\\Private"):
        assert value not in console
        assert value not in report
    assert json.loads(report)["status"] == "failed"


def test_render_failure_gives_ukrainian_stage_evidence_and_next_action():
    error = b.command_failure(windows_error(errno.EACCES, 32), "venv-create")
    rendered = b.render_failure(error)
    assert "Створення окремого середовища .venv-v2 [venv-create]" in rendered
    assert "[file-locked]" in rendered
    assert "WinError=32" in rendered
    assert "errno=" in rendered
    assert "Закрийте інші вікна бота" in rendered
    assert "новий install-report.json" in rendered
    assert "private-token" not in rendered


def test_wrapped_lock_disk_error_does_not_claim_another_bot_is_running(tmp_path):
    error = RuntimeError(PRIVATE)
    error.__cause__ = OSError(errno.ENOSPC, PRIVATE)
    normalized = b.normalize_failure(error)
    assert normalized.category == "disk-full"
    assert normalized.errno == errno.ENOSPC
    rendered = b.render_failure(error)
    assert "Недостатньо місця" in rendered
    assert "private-token" not in rendered
    b.write_failure_report(tmp_path, error)
    report = json.loads((tmp_path / "data/v2/install-report.json").read_text(encoding="utf-8"))
    assert report["category"] == "disk-full"
    assert report["errno"] == errno.ENOSPC


def test_unknown_stage_and_category_cannot_leak_into_console_or_report(tmp_path):
    error = b.BootstrapError("safe message", stage=PRIVATE, category=PRIVATE)
    rendered = b.render_failure(error)
    assert "[bootstrap]" in rendered
    assert "[unknown]" in rendered
    b.write_failure_report(tmp_path, error)
    report = (tmp_path / "data/v2/install-report.json").read_text(encoding="utf-8")
    assert "private-token" not in report
    assert "private-token" not in rendered
    assert json.loads(report)["stage"] == "bootstrap"
    assert json.loads(report)["category"] == "unknown"


@pytest.mark.parametrize("category", ["access-denied", "file-locked", "execution-policy", "timeout"])
def test_dependency_probe_external_failure_does_not_trigger_package_reinstall(tmp_path, monkeypatch, category):
    root = tmp_path.resolve()
    (root / "core_v2").mkdir()
    requirements = root / "core_v2/requirements.txt"
    requirements.write_bytes(b"unused==1\n")
    interpreter = b.python_path(root)
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    stamp = root / ".venv-v2/yeboy-requirements.sha256"
    stamp.write_text(hashlib.sha256(requirements.read_bytes()).hexdigest(), encoding="ascii")
    monkeypatch.setattr(b.sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(b, "validate_environment", Mock())
    monkeypatch.setattr(b, "ensure_pip", Mock())
    failure = b.BootstrapError("safe", stage="dependency-check", category=category)
    calls = []

    def run(args, _root):
        calls.append(args)
        assert "install" not in args, "External refusal must not cause package changes"
        raise failure

    monkeypatch.setattr(b, "run", run)
    with pytest.raises(b.BootstrapError) as caught:
        b.prepare(root)
    assert caught.value is failure
    assert len(calls) == 1
