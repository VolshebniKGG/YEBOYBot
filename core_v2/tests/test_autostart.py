"""Task Scheduler tests never register real tasks; Windows smoke only queries."""
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from tools.launcher import autostart as a

SID = "S-1-5-21-1234-5678-9012-1001"
OTHER_SID = "S-1-5-21-1234-5678-9012-1002"


@pytest.fixture
def context(tmp_path, monkeypatch):
    root = (tmp_path / "Бот & з пробілами").resolve()
    root.mkdir()
    monkeypatch.setattr(a, "_windows", lambda: True)
    monkeypatch.setattr(a, "_sid", lambda root: SID)
    result = a._context(root)
    result["python"].parent.mkdir(parents=True)
    result["python"].touch()
    result["pythonw"].touch()
    monkeypatch.setattr(a.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0))
    return result


class Scheduler:
    def __init__(self, xml=None):
        self.xml = xml
        self.calls = []

    def run(self, tool, *arguments, **kwargs):
        self.calls.append((tool, arguments, kwargs))
        if arguments[0] == "/Query":
            return subprocess.CompletedProcess(arguments, 1 if self.xml is None else 0,
                                               stdout=self.xml or b"", stderr=b"not used")
        if arguments[0] == "/Create":
            if self.xml is not None:
                return subprocess.CompletedProcess(arguments, 1)
            self.xml = Path(arguments[arguments.index("/XML") + 1]).read_bytes()
        elif arguments[0] == "/Change":
            task = ET.fromstring(self.xml)
            task.find(".//" + a._tag("Settings")).find(a._tag("Enabled")).text = "true"
            self.xml = ET.tostring(task)
        elif arguments[0] == "/Delete":
            self.xml = None
        return subprocess.CompletedProcess(arguments, 0)

    def operations(self):
        return [arguments[0] for _, arguments, _ in self.calls]


def alter(context, field, value):
    task = ET.fromstring(a.build_xml(context["root"], SID))
    node = task.find(".//" + a._tag(field))
    node.text = value
    return ET.tostring(task)


def test_xml_has_logon_sid_privilege_and_explicit_action(context):
    xml = a.build_xml(context["root"], SID)
    assert xml.startswith(b"\xff\xfe")
    task = ET.fromstring(xml)
    assert task.find(".//" + a._tag("Command")).text == str(context["pythonw"])
    assert task.find(".//" + a._tag("Arguments")).text == "-m core_v2.managed"
    assert task.find(".//" + a._tag("WorkingDirectory")).text == str(context["root"])
    assert task.find(".//" + a._tag("LogonType")).text == "InteractiveToken"
    assert task.find(".//" + a._tag("RunLevel")).text == "LeastPrivilege"
    assert task.find(".//" + a._tag("MultipleInstancesPolicy")).text == "IgnoreNew"
    assert task.find(".//" + a._tag("ExecutionTimeLimit")).text == "PT0S"
    assert task.find(".//" + a._tag("RestartOnFailure")) is None
    assert [node.text for node in task.findall(".//" + a._tag("UserId"))] == [SID, SID]
    assert a._inspect(xml, context) == "enabled"


def test_task_names_are_stable_and_isolated_per_root_and_sid(tmp_path):
    first = a.task_name(tmp_path, SID)
    assert first == a.task_name(tmp_path / ".", SID)
    assert first != a.task_name(tmp_path / "other", SID)
    assert first != a.task_name(tmp_path, OTHER_SID)
    assert first.startswith("\\YEBOYBot-CoreV2-")
    assert str(tmp_path) not in first


@pytest.mark.parametrize("value", ["", "user", "S-1-5\n/F", "S-1-5 & command", None])
def test_invalid_sid_cannot_form_task_name(tmp_path, value):
    with pytest.raises(a.AutostartError):
        a.task_name(tmp_path, value)


@pytest.mark.parametrize("field,value", [
    ("Description", "unrelated task"), ("Command", r"C:\unrelated\pythonw.exe"),
    ("Arguments", "-m core_v2.managed --untrusted"), ("WorkingDirectory", r"C:\other"),
    ("UserId", OTHER_SID), ("LogonType", "Password"), ("RunLevel", "HighestAvailable"),
    ("ExecutionTimeLimit", "PT72H"), ("MultipleInstancesPolicy", "Parallel"),
])
def test_changed_ownership_fields_block_enable_and_delete(context, monkeypatch, field, value):
    scheduler = Scheduler(alter(context, field, value))
    monkeypatch.setattr(a, "_run", scheduler.run)
    assert a.state(context["root"])["status"] == "foreign"
    for operation in (a.enable, a.disable):
        with pytest.raises(a.AutostartError):
            operation(context["root"], confirmed=True)
    assert set(scheduler.operations()) == {"/Query"}


@pytest.mark.parametrize("extra", ["action", "principal", "trigger", "restart", "privilege"])
def test_extra_actions_principals_triggers_or_restart_are_foreign(context, extra):
    task = ET.fromstring(a.build_xml(context["root"], SID))
    if extra == "action":
        ET.SubElement(task.find(a._tag("Actions")), a._tag("ComHandler"))
    elif extra == "principal":
        ET.SubElement(task.find(a._tag("Principals")), a._tag("Principal"))
    elif extra == "trigger":
        ET.SubElement(task.find(a._tag("Triggers")), a._tag("TimeTrigger"))
    elif extra == "restart":
        ET.SubElement(task.find(a._tag("Settings")), a._tag("RestartOnFailure"))
    else:
        ET.SubElement(task.find(".//" + a._tag("Principal")), a._tag("RequiredPrivileges"))
    assert a._inspect(ET.tostring(task), context) == "foreign"


@pytest.mark.parametrize("payload", [b"<bad", b"x" * (512 * 1024 + 1)])
def test_unparseable_or_oversize_xml_is_unknown(context, payload):
    assert a._inspect(payload, context) == "unknown"


def test_query_failure_remains_unknown_and_never_authorizes_delete(context, monkeypatch):
    scheduler = Scheduler()
    monkeypatch.setattr(a, "_run", scheduler.run)
    assert a.state(context["root"])["status"] == "unknown"
    with pytest.raises(a.AutostartError):
        a.disable(context["root"], confirmed=True)
    assert set(scheduler.operations()) == {"/Query"}


def test_create_requires_consent_and_never_forces_overwrite(context, monkeypatch):
    scheduler = Scheduler()
    monkeypatch.setattr(a, "_run", scheduler.run)
    with pytest.raises(a.AutostartError):
        a.enable(context["root"])
    assert scheduler.calls == []
    outcome = a.enable(context["root"], confirmed=True)
    assert outcome["status"] == "enabled"
    assert scheduler.operations() == ["/Query", "/Create", "/Query"]
    create = scheduler.calls[1][1]
    assert "/F" not in create
    assert create[create.index("/TN") + 1] == context["name"]
    assert a._inspect(scheduler.xml, context) == "enabled"


def test_existing_owned_enabled_task_is_idempotent(context, monkeypatch):
    scheduler = Scheduler(a.build_xml(context["root"], SID))
    monkeypatch.setattr(a, "_run", scheduler.run)
    assert a.enable(context["root"], confirmed=True)["status"] == "enabled"
    assert scheduler.operations() == ["/Query"]


def test_existing_disabled_task_is_revalidated_before_enabling(context, monkeypatch):
    task = ET.fromstring(a.build_xml(context["root"], SID))
    task.find(a._tag("Settings")).find(a._tag("Enabled")).text = "false"
    scheduler = Scheduler(ET.tostring(task))
    monkeypatch.setattr(a, "_run", scheduler.run)
    assert a.state(context["root"])["status"] == "disabled"
    assert a.enable(context["root"], confirmed=True)["status"] == "enabled"
    assert scheduler.operations() == ["/Query", "/Query", "/Query", "/Change", "/Query"]


def test_task_changed_after_preflight_is_not_enabled(context, monkeypatch):
    statuses = iter(["disabled", "foreign"])
    monkeypatch.setattr(a, "_query", lambda context: next(statuses))
    scheduler = Scheduler()
    monkeypatch.setattr(a, "_run", scheduler.run)
    with pytest.raises(a.AutostartError):
        a.enable(context["root"], confirmed=True)
    assert scheduler.calls == []


def test_delete_requires_consent_and_exact_owned_task(context, monkeypatch):
    scheduler = Scheduler(a.build_xml(context["root"], SID))
    monkeypatch.setattr(a, "_run", scheduler.run)
    with pytest.raises(a.AutostartError):
        a.disable(context["root"])
    assert scheduler.calls == []
    assert a.disable(context["root"], confirmed=True)["status"] == "removed"
    assert scheduler.operations() == ["/Query", "/Delete"]
    delete = scheduler.calls[1][1]
    assert delete == ("/Delete", "/TN", context["name"], "/F")
    # A later failed query is unknown, even after successful removal.
    assert a.state(context["root"])["status"] == "unknown"


@pytest.mark.parametrize("operation", ["/Create", "/Delete"])
def test_scheduler_write_failure_never_reports_success(context, monkeypatch, operation):
    scheduler = Scheduler(None if operation == "/Create" else a.build_xml(context["root"], SID))
    def fail(tool, *arguments, **kwargs):
        if arguments[0] == operation:
            return subprocess.CompletedProcess(arguments, 1, stderr=b"private error text")
        return scheduler.run(tool, *arguments, **kwargs)
    monkeypatch.setattr(a, "_run", fail)
    method = a.enable if operation == "/Create" else a.disable
    with pytest.raises(a.AutostartError) as raised:
        method(context["root"], confirmed=True)
    assert "private error text" not in str(raised.value)


def test_unconfirmed_readback_never_reports_enabled(context, monkeypatch):
    queries = iter(["unknown", "unknown"])
    monkeypatch.setattr(a, "_query", lambda context: next(queries))
    scheduler = Scheduler()
    monkeypatch.setattr(a, "_run", scheduler.run)
    with pytest.raises(a.AutostartError):
        a.enable(context["root"], confirmed=True)
    assert scheduler.operations() == ["/Create"]


@pytest.mark.parametrize("missing", ["python", "pythonw"])
def test_missing_interpreter_prevents_registration(context, monkeypatch, missing):
    context[missing].unlink()
    scheduler = Scheduler()
    monkeypatch.setattr(a, "_run", scheduler.run)
    with pytest.raises(a.AutostartError):
        a.enable(context["root"], confirmed=True)
    assert scheduler.operations() == ["/Query"]


def test_preflight_failure_hides_output_and_never_registers(context, monkeypatch):
    scheduler = Scheduler()
    monkeypatch.setattr(a, "_run", scheduler.run)
    monkeypatch.setattr(a.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1,
                        stdout=b"private token", stderr=b"private URL"))
    with pytest.raises(a.AutostartError) as raised:
        a.enable(context["root"], confirmed=True)
    assert "private" not in str(raised.value)
    assert scheduler.operations() == ["/Query"]


def test_preflight_captures_doctor_and_import_output(context, monkeypatch):
    calls = []
    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 0)
    monkeypatch.setattr(a.subprocess, "run", run)
    a._preflight(context)
    assert calls[0][0] == [str(context["python"]), "-m", "core_v2.doctor"]
    assert calls[1][0][1] == "-c"
    assert all(options["stdin"] == subprocess.DEVNULL and options["capture_output"] and
               options["timeout"] == 30 and options["cwd"] == context["root"] for _, options in calls)


def test_system_tools_use_absolute_path_no_shell_stdin_and_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "_windows", lambda: True)
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    calls = []
    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 1, stdout=b"", stderr=b"private")
    monkeypatch.setattr(a.subprocess, "run", run)
    a._run("schtasks.exe", "/Query", "/TN", "\\exact-task", "/XML", root=tmp_path)
    arguments, options = calls[0]
    assert arguments == [str(tmp_path / "System32/schtasks.exe"), "/Query", "/TN", "\\exact-task", "/XML"]
    assert options["stdin"] == subprocess.DEVNULL and options["capture_output"]
    assert options["timeout"] == 15
    assert not options.get("shell", False)


@pytest.mark.parametrize("failure", [OSError("private-path"), subprocess.TimeoutExpired("private command", 15)])
def test_timeout_and_os_errors_are_redacted(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(a, "_windows", lambda: True)
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(a.subprocess, "run", fail)
    with pytest.raises(a.AutostartError) as raised:
        a._run("schtasks.exe", "/Query", root=tmp_path)
    assert "private" not in str(raised.value)


@pytest.mark.parametrize("payload", [
    b'"account","S-1-5-21-1234-5678-9012-1001"\r\n',
    '"користувач","S-1-5-21-1234-5678-9012-1001"\r\n'.encode("utf-16"),
])
def test_sid_uses_csv_and_ignores_localized_account_name(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(a, "_run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=payload))
    assert a._sid(tmp_path) == SID


@pytest.mark.parametrize("payload", [b'"account","not-sid"', b'"one","S-1-5-21"\n"two","S-1-5-22"', b"x" * 16385])
def test_invalid_whoami_output_is_refused(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(a, "_run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=payload))
    with pytest.raises(a.AutostartError):
        a._sid(tmp_path)


def test_non_windows_state_is_unsupported_without_system_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "_windows", lambda: False)
    monkeypatch.setattr(a, "_run", lambda *args, **kwargs: pytest.fail("no Windows calls on this platform"))
    assert a.state(tmp_path)["status"] == "unsupported"
    with pytest.raises(a.AutostartError):
        a.enable(tmp_path, confirmed=True)


@pytest.mark.parametrize("choice", ["1", "2"])
def test_menu_cancel_never_changes_scheduler(tmp_path, monkeypatch, choice):
    monkeypatch.setattr(a, "state", lambda root: {"status": "unknown", "task_name": "test"})
    replies = iter([choice, "скасувати"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(replies))
    monkeypatch.setattr(a, "enable", lambda *args, **kwargs: pytest.fail("cancel must not enable"))
    monkeypatch.setattr(a, "disable", lambda *args, **kwargs: pytest.fail("cancel must not delete"))
    a.menu(tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows read-only schtasks smoke")
def test_windows_read_only_query_missing_task_from_unicode_path(tmp_path):
    root = tmp_path / "Бот з пробілами"
    root.mkdir()
    # Unique temporary root means no legitimate task was registered for this identity.
    # state() invokes only whoami /user and schtasks /Query, never /Create or /Delete.
    result = a.state(root)
    assert result["status"] == "unknown"
    if result["task_name"] is not None:
        assert result["task_name"].startswith("\\YEBOYBot-CoreV2-")
