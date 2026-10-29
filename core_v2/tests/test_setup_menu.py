"""Setup handoff exits Python before an installer can replace loaded libraries."""
from unittest.mock import Mock

import pytest

from tools.launcher import __main__ as launcher


@pytest.fixture
def no_children(monkeypatch):
    call = Mock(side_effect=AssertionError("Python menu must exit before setup starts"))
    monkeypatch.setattr(launcher.subprocess, "call", call)
    return call


@pytest.fixture
def menu(tmp_path, monkeypatch):
    from tools.launcher import process

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(process, "state", lambda: {"status": "OFFLINE"})

    def select(*choices):
        read = Mock(side_effect=choices)
        monkeypatch.setattr("builtins.input", read)
        return read

    return select


def test_non_windows_rejects_setup_without_starting_process(monkeypatch, no_children, capsys):
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    assert launcher.full_setup() == 1
    no_children.assert_not_called()
    assert "лише у Windows" in capsys.readouterr().out


def test_windows_setup_requests_outer_launcher_handoff_instead_of_child_process(
        monkeypatch, no_children, capsys):
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.delenv("SystemRoot", raising=False)
    assert launcher.full_setup() == 24
    no_children.assert_not_called()
    assert "головному вікні YEBOYBot.cmd" in capsys.readouterr().out


def test_windows_menu_returns_handoff_code_immediately_after_selection(
        menu, monkeypatch, no_children):
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    read = menu("24")
    assert launcher.main() == 24
    read.assert_called_once()
    no_children.assert_not_called()


def test_non_windows_menu_keeps_running_after_rejected_setup(
        menu, monkeypatch, no_children, capsys):
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    read = menu("24", "0")
    assert launcher.main() == 0
    assert read.call_count == 2
    no_children.assert_not_called()
    assert "лише у Windows" in capsys.readouterr().out


def test_cancel_menu_does_not_request_setup_and_preserves_existing_actions(
        menu, monkeypatch, no_children, capsys):
    setup = Mock(side_effect=AssertionError("Exit must not request setup"))
    monkeypatch.setattr(launcher, "full_setup", setup)
    menu("0")
    assert launcher.main() == 0
    setup.assert_not_called()
    no_children.assert_not_called()
    output = capsys.readouterr().out
    assert "[24] Повне встановлення / відновлення Core V2" in output
    assert "[5] Оновити Python-залежності" in output
    assert "[12] Початкове налаштування" in output


def test_rejected_setup_result_does_not_end_the_menu(menu, monkeypatch, no_children):
    setup = Mock(return_value=1)
    monkeypatch.setattr(launcher, "full_setup", setup)
    read = menu("24", "0")
    assert launcher.main() == 0
    assert read.call_count == 2
    setup.assert_called_once_with()
    no_children.assert_not_called()


@pytest.mark.parametrize("exit_code", [24, 25])
def test_other_operation_with_same_exit_code_does_not_trigger_setup_handoff(menu, monkeypatch, exit_code):
    run = Mock(return_value=exit_code)
    monkeypatch.setattr(launcher, "run", run)
    read = menu("4", "0")
    assert launcher.main() == 0
    assert read.call_count == 2
    run.assert_called_once_with("-m", "tools.launcher.diagnostics")


def test_windows_repair_requests_handoff_without_launching_pip(monkeypatch, no_children, capsys):
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    assert launcher.repair_dependencies() == 25
    no_children.assert_not_called()
    assert "головному вікні YEBOYBot.cmd" in capsys.readouterr().out


def test_windows_menu_returns_repair_handoff_before_next_input(menu, monkeypatch, no_children):
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    read = menu("5")
    assert launcher.main() == 25
    read.assert_called_once()
    no_children.assert_not_called()


def test_non_windows_repair_preserves_existing_bootstrap_call_and_exit_code(monkeypatch):
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    run = Mock(return_value=7)
    monkeypatch.setattr(launcher, "run", run)
    assert launcher.repair_dependencies() == 7
    run.assert_called_once_with("-m", "tools.launcher.bootstrap", "--repair")


def test_non_windows_bootstrap_exit_code_cannot_be_mistaken_for_windows_repair_handoff(menu, monkeypatch):
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    run = Mock(return_value=25)
    monkeypatch.setattr(launcher, "run", run)
    read = menu("5", "0")
    assert launcher.main() == 0
    assert read.call_count == 2
    run.assert_called_once_with("-m", "tools.launcher.bootstrap", "--repair")
