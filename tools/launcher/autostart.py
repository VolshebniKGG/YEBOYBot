"""Opt-in Windows logon task with explicit ownership and no saved credentials."""
import csv
import hashlib
import io
import ntpath
import os
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
ARGUMENTS = "-m core_v2.managed"
SID_PATTERN = re.compile(r"S-1-(?:[0-9]+-)+[0-9]+")
DESCRIPTION_PREFIX = "YEBOYBot Core V2 autostart/v1:"
ET.register_namespace("", NAMESPACE)


class AutostartError(RuntimeError):
    pass


def _windows():
    return os.name == "nt"


def _run(tool, *arguments, root=ROOT, timeout=15):
    if not _windows():
        raise AutostartError("Автозапуск через Планувальник доступний лише у Windows.")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / tool
    try:
        return subprocess.run([str(executable), *map(str, arguments)], cwd=root,
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # Command output and exception text can contain user paths and configuration.
        raise AutostartError("Windows не виконала операцію або час очікування вичерпано.") from exc


def _sid(root):
    response = _run("whoami.exe", "/user", "/fo", "csv", "/nh", root=root)
    if response.returncode:
        raise AutostartError("Не вдалося визначити SID поточного користувача Windows.")
    raw = response.stdout
    if len(raw) > 16384:
        raise AutostartError("Неправильна відповідь Windows для поточного користувача.")
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    # CSV delimiters and SID are ASCII; localized account names are not used.
    rows = [row for row in csv.reader(io.StringIO(raw.decode(encoding, errors="replace"))) if row]
    if len(rows) != 1 or len(rows[0]) != 2 or not SID_PATTERN.fullmatch(rows[0][1]):
        raise AutostartError("Неправильна відповідь Windows для поточного користувача.")
    return rows[0][1]


def task_name(root, sid):
    if not isinstance(sid, str) or not SID_PATTERN.fullmatch(sid):
        raise AutostartError("Неправильний SID Windows.")
    normalized = ntpath.normcase(str(Path(root).resolve()))
    digest = hashlib.sha256((normalized + "\0" + sid).encode("utf-8")).hexdigest()[:24]
    return "\\YEBOYBot-CoreV2-" + digest


def _context(root):
    if not _windows():
        raise AutostartError("Автозапуск через Планувальник доступний лише у Windows.")
    root = Path(root).resolve()
    sid = _sid(root)
    name = task_name(root, sid)
    return {"root": root, "sid": sid, "name": name,
            "description": DESCRIPTION_PREFIX + name.rsplit("-", 1)[1],
            "python": root / ".venv-v2/Scripts/python.exe",
            "pythonw": root / ".venv-v2/Scripts/pythonw.exe"}


def _tag(name):
    return "{" + NAMESPACE + "}" + name


def _element(parent, tag, text=None, **attributes):
    node = ET.SubElement(parent, _tag(tag), attributes)
    node.text = text
    return node


def build_xml(root, sid):
    root = Path(root).resolve()
    name = task_name(root, sid)
    task = ET.Element(_tag("Task"), {"version": "1.2"})
    registration = _element(task, "RegistrationInfo")
    _element(registration, "Description", DESCRIPTION_PREFIX + name.rsplit("-", 1)[1])
    trigger = _element(_element(task, "Triggers"), "LogonTrigger")
    _element(trigger, "Enabled", "true")
    _element(trigger, "UserId", sid)
    principal = _element(_element(task, "Principals"), "Principal", id="Author")
    _element(principal, "UserId", sid)
    _element(principal, "LogonType", "InteractiveToken")
    _element(principal, "RunLevel", "LeastPrivilege")
    settings = _element(task, "Settings")
    for key, value in (("MultipleInstancesPolicy", "IgnoreNew"),
                       ("DisallowStartIfOnBatteries", "false"),
                       ("StopIfGoingOnBatteries", "false"),
                       ("AllowHardTerminate", "false"), ("StartWhenAvailable", "true"),
                       ("RunOnlyIfNetworkAvailable", "false"), ("Enabled", "true"),
                       ("Hidden", "false"), ("ExecutionTimeLimit", "PT0S")):
        _element(settings, key, value)
    action = _element(_element(task, "Actions", Context="Author"), "Exec")
    _element(action, "Command", str(root / ".venv-v2/Scripts/pythonw.exe"))
    _element(action, "Arguments", ARGUMENTS)
    _element(action, "WorkingDirectory", str(root))
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def _text(parent, tag):
    values = parent.findall(_tag(tag))
    return values[0].text if len(values) == 1 else None


def _path_matches(value, expected):
    return isinstance(value, str) and ntpath.normcase(ntpath.normpath(value)) == ntpath.normcase(ntpath.normpath(str(expected)))


def _inspect(xml, context):
    if len(xml) > 512 * 1024:
        return "unknown"
    try:
        task = ET.fromstring(xml)
    except (ET.ParseError, ValueError):
        return "unknown"
    if task.tag != _tag("Task"):
        return "foreign"
    registrations = task.findall(_tag("RegistrationInfo"))
    principals = task.findall(_tag("Principals"))
    actions = task.findall(_tag("Actions"))
    triggers = task.findall(_tag("Triggers"))
    settings = task.findall(_tag("Settings"))
    if not all(len(nodes) == 1 for nodes in (registrations, principals, actions, triggers, settings)):
        return "foreign"
    if _text(registrations[0], "Description") != context["description"]:
        return "foreign"
    principal_list = list(principals[0])
    action_list = list(actions[0])
    trigger_list = list(triggers[0])
    if len(principal_list) != 1 or principal_list[0].tag != _tag("Principal"):
        return "foreign"
    principal = principal_list[0]
    if (principal.attrib.get("id") != "Author" or _text(principal, "UserId") != context["sid"] or
            _text(principal, "LogonType") != "InteractiveToken" or
            _text(principal, "RunLevel") != "LeastPrivilege" or
            any(node.tag not in {_tag("UserId"), _tag("LogonType"), _tag("RunLevel")} for node in principal)):
        return "foreign"
    if len(action_list) != 1 or action_list[0].tag != _tag("Exec") or actions[0].attrib.get("Context") != "Author":
        return "foreign"
    action = action_list[0]
    if (not _path_matches(_text(action, "Command"), context["pythonw"]) or
            _text(action, "Arguments") != ARGUMENTS or
            not _path_matches(_text(action, "WorkingDirectory"), context["root"]) or
            any(node.tag not in {_tag("Command"), _tag("Arguments"), _tag("WorkingDirectory")} for node in action)):
        return "foreign"
    if len(trigger_list) != 1 or trigger_list[0].tag != _tag("LogonTrigger"):
        return "foreign"
    trigger = trigger_list[0]
    if (_text(trigger, "UserId") != context["sid"] or _text(trigger, "Enabled") != "true" or
            any(node.tag not in {_tag("UserId"), _tag("Enabled")} for node in trigger)):
        return "foreign"
    settings = settings[0]
    if (_text(settings, "MultipleInstancesPolicy") != "IgnoreNew" or
            _text(settings, "ExecutionTimeLimit") != "PT0S" or
            settings.find(_tag("RestartOnFailure")) is not None):
        return "foreign"
    enabled = _text(settings, "Enabled")
    return {"true": "enabled", "false": "disabled"}.get(enabled, "unknown")


def _query(context):
    try:
        response = _run("schtasks.exe", "/Query", "/TN", context["name"], "/XML", root=context["root"])
    except AutostartError:
        return "unknown"
    # Missing, denied and unavailable cannot be distinguished by localized stderr.
    return _inspect(response.stdout, context) if response.returncode == 0 else "unknown"


def state(root=ROOT):
    if not _windows():
        return {"status": "unsupported", "task_name": None}
    try:
        context = _context(root)
        return {"status": _query(context), "task_name": context["name"]}
    except (AutostartError, OSError):
        return {"status": "unknown", "task_name": None}


def _preflight(context):
    if not context["python"].is_file() or not context["pythonw"].is_file():
        raise AutostartError("Спершу встановіть середовище через YEBOYBot.cmd; потрібні python.exe та pythonw.exe.")
    for arguments in (("-m", "core_v2.doctor"),
                      ("-c", "import discord, sqlalchemy, aiosqlite, asyncpg, dotenv")):
        try:
            response = subprocess.run([str(context["python"]), *arguments], cwd=context["root"],
                                      stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AutostartError("Перевірка середовища не завершилася; автозапуск не змінено.") from exc
        if response.returncode:
            raise AutostartError("Перевірка конфігурації або імпортів не пройдена; скористайтеся діагностикою меню.")


def enable(root=ROOT, *, confirmed=False):
    if not confirmed:
        raise AutostartError("Увімкнення автозапуску потребує явного підтвердження.")
    context = _context(root)
    current = _query(context)
    if current == "foreign":
        raise AutostartError("Ім'я завдання зайняте чужим або зміненим завданням. Його не буде перезаписано.")
    _preflight(context)
    if current == "enabled":
        return {"status": "enabled", "task_name": context["name"]}
    if current == "disabled":
        if _query(context) != "disabled":
            raise AutostartError("Завдання змінилося. Повторіть перевірку автозапуску.")
        response = _run("schtasks.exe", "/Change", "/TN", context["name"], "/ENABLE", root=context["root"])
    else:
        # No /F: duplicate tasks must fail, even if Query failed due to access denial.
        with tempfile.TemporaryDirectory(prefix="yeboy-autostart-") as temporary:
            path = Path(temporary) / "task.xml"
            path.write_bytes(build_xml(context["root"], context["sid"]))
            response = _run("schtasks.exe", "/Create", "/TN", context["name"], "/XML", path, root=context["root"])
    if response.returncode:
        raise AutostartError("Windows не увімкнула завдання. Наявне завдання не перезаписувалося.")
    if _query(context) != "enabled":
        raise AutostartError("Операцію виконано, але стан завдання не підтверджено. Перевірте Планувальник Windows.")
    return {"status": "enabled", "task_name": context["name"]}


def disable(root=ROOT, *, confirmed=False):
    if not confirmed:
        raise AutostartError("Вимкнення автозапуску потребує явного підтвердження.")
    context = _context(root)
    if _query(context) not in {"enabled", "disabled"}:
        raise AutostartError("Належність завдання не підтверджено; жодне завдання не видалено.")
    response = _run("schtasks.exe", "/Delete", "/TN", context["name"], "/F", root=context["root"])
    if response.returncode:
        raise AutostartError("Windows не видалила завдання. Перевірте Планувальник Windows.")
    return {"status": "removed", "task_name": context["name"]}


def menu(root=ROOT):
    labels = {"enabled": "Увімкнено", "disabled": "Вимкнено", "removed": "Завдання видалено",
              "unknown": "Невідомо", "foreign": "Чуже або змінене завдання", "unsupported": "Недоступно в цій ОС"}
    current = state(root)
    print("Автозапуск:", labels[current["status"]])
    print("Це стан Планувальника, а не готовність Discord. Запуск — після входу поточного користувача.")
    if current["status"] == "unsupported":
        return
    print("[1] Увімкнути автозапуск  [2] Вимкнути та видалити власне завдання  [0] Назад")
    choice = input("Виберіть дію: ").strip()
    if choice not in {"1", "2"}:
        return
    phrase = "УВІМКНУТИ АВТОЗАПУСК" if choice == "1" else "ВИМКНУТИ АВТОЗАПУСК"
    print("Буде створено/увімкнено завдання без прав адміністратора й збереження пароля." if choice == "1" else
          "Буде видалено тільки перевірене завдання цієї копії. Працюючий бот не зупиняється.")
    if input(f"Для підтвердження введіть {phrase}: ").strip() != phrase:
        print("Скасовано.")
        return
    try:
        outcome = enable(root, confirmed=True) if choice == "1" else disable(root, confirmed=True)
        print("Автозапуск:", labels[outcome["status"]])
    except AutostartError as exc:
        print(str(exc))
