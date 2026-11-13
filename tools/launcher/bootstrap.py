"""Repeatable Windows installation using only the standard library initially."""
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import venv
from datetime import UTC, datetime
from pathlib import Path

from core_v2.runtime import InstanceLock
from tools.installer_policy import (
    PIP_VERSION,
    PIP_VERSION_CHECK,
    clean_environment,
    pip_command,
    pip_is_current,
)

ROOT = Path(__file__).resolve().parents[2]
IMPORT_CHECK = "import discord, sqlalchemy, aiosqlite, asyncpg, dotenv, PIL, nacl, davey, yt_dlp, yt_dlp_ejs"


STAGE_LABELS = {
    "bootstrap": "Підготовка встановлення",
    "venv-create": "Створення окремого середовища .venv-v2",
    "environment-validation": "Перевірка файлів та ізоляції середовища",
    "environment-probe": "Запуск Python із .venv-v2",
    "pip-version": "Перевірка версії pip",
    "ensurepip": "Встановлення pip із локального комплекту Python",
    "pip-upgrade": "Оновлення pip через мережу",
    "dependency-install": "Встановлення бібліотек Core V2",
    "dependency-check": "Перевірка сумісності бібліотек",
    "library-import": "Перевірка завантаження бібліотек",
    "subprocess": "Запуск допоміжної перевірки",
}
CATEGORY_LABELS = {
    "unknown": "Причину ще не встановлено",
    "filesystem": "Системна помилка файлів або запуску",
    "access-denied": "Відмовлено в доступі",
    "file-locked": "Файл зайнятий або заблокований іншим процесом",
    "security-blocked": "Windows повідомила про блокування засобом захисту",
    "execution-policy": "Виконання заборонено політикою або перевіркою підпису",
    "disk-full": "Недостатньо місця на диску",
    "path-too-long": "Занадто довгий шлях",
    "missing-file": "Не знайдено потрібний файл",
    "invalid-executable": "Непридатний виконуваний файл",
    "invalid-environment": "Не підтверджено цілісність або ізоляцію середовища",
    "timeout": "Перевищено час очікування",
    "certificate": "Не підтверджено TLS-сертифікат",
    "proxy": "Помилка з'єднання через проксі",
    "network": "Помилка мережевого з'єднання",
    "tls": "Помилка захищеного з'єднання",
    "broken-interpreter": "Непрацездатний Python середовища",
    "missing-module": "Не знайдено потрібний модуль",
    "package-unavailable": "Не вдалося отримати потрібний пакет",
    "probe-syntax": "Помилка коду перевірки Python",
}
NO_AUTOMATIC_REPAIR = frozenset({
    "access-denied", "file-locked", "execution-policy", "security-blocked",
    "disk-full", "path-too-long", "filesystem", "timeout",
})
# Only this explicit signature is evidence of an owned venv pointing at a
# missing Python install. An unknown process failure or syntax/import error
# must never cause a rename and duplicate venv creation.
REBUILDABLE_ENVIRONMENT_CATEGORIES = frozenset({"broken-interpreter"})


class BootstrapError(RuntimeError):
    def __init__(self, message, *, stage="bootstrap", category="unknown", exit_code=None,
                 errno=None, winerror=None):
        super().__init__(message)
        self.stage, self.category, self.exit_code = stage, category, exit_code
        self.errno, self.winerror = errno, winerror


def numeric_code(value):
    """Only numeric system codes may cross the diagnostic boundary."""
    return value if type(value) is int else None


def os_failure_hint(error):
    """Classify OS failures without depending on the Windows display language."""
    win = numeric_code(getattr(error, "winerror", None))
    code = numeric_code(getattr(error, "errno", None))
    if win in {225, 226}:
        return "security-blocked", (
            "Windows повідомила про блокування або видалення файла засобом захисту. "
            "Перегляньте Безпека Windows → Журнал захисту. Не вимикайте захист і не додавайте виключення автоматично.")
    if win in {577, 1260, 4551}:
        return "execution-policy", (
            "Windows заборонила виконання за політикою або перевіркою підпису. "
            "Перевірте журнал контролю програм; на керованому ПК зверніться до адміністратора. "
            "Інсталятор не змінює політики захисту.")
    # Prefer the specific Windows code over its lossy POSIX errno mapping.
    if win is not None:
        code = None
    if win in {32, 33} or code == errno.EBUSY:
        return "file-locked", (
            "Файл зайнятий або заблокований іншим процесом. Закрийте інші вікна бота та інсталятори, "
            "дочекайтеся завершення їхньої роботи й повторіть встановлення. Дані не видаляйте.")
    if win == 5 or code in {errno.EACCES, errno.EPERM}:
        return "access-denied", (
            "Windows або файлова система відмовила в доступі. Закрийте інші вікна бота й перевірте "
            "права на його папку та Журнал захисту Windows. Цей код сам по собі не доводить блокування антивірусом.")
    if win in {39, 112} or code == errno.ENOSPC:
        return "disk-full", "Недостатньо вільного місця для .venv-v2 або тимчасових файлів Python."
    if win == 206 or code == errno.ENAMETOOLONG:
        return "path-too-long", "Шлях до середовища занадто довгий. Розпакуйте повний ZIP у коротший шлях і повторіть налаштування."
    if win in {2, 3} or code == errno.ENOENT:
        return "missing-file", (
            "Не знайдено потрібний файл Python або середовища. Перевірте цілісність встановлення Python 3.12 "
            "та Журнал захисту Windows; не видаляйте конфігурацію чи БД бота.")
    if win in {193, 216} or code == errno.ENOEXEC:
        return "invalid-executable", "Файл Python не є придатним виконуваним файлом. Перевірте офіційне встановлення Python 3.12 x64."
    return "filesystem", "Системна помилка роботи з файлами або запуску Python. Збережіть наведений числовий код і install-report.json."


def command_failure(error, stage):
    """Never interpolate raw exceptions, paths, commands or subprocess output."""
    win = numeric_code(getattr(error, "winerror", None))
    code = numeric_code(getattr(error, "errno", None))
    exit_code = numeric_code(getattr(error, "returncode", None))
    if isinstance(error, subprocess.TimeoutExpired):
        category, hint = "timeout", "Перевищено час очікування Python. Перевірте завантаження ПК і журнал захисту; автоматичного повтору немає."
    elif isinstance(error, OSError):
        category, hint = os_failure_hint(error)
    else:
        output = str(getattr(error, "output", "") or "") + "\n" + str(getattr(error, "stderr", "") or "")
        win = reported_winerror(output)
        category, hint = failure_hint(output)
    if stage == "environment-probe" and category == "access-denied" and isinstance(error, OSError):
        hint = (
            "Системний Python працює, але Windows відмовила у запуску Python "
            "з ізольованої .venv-v2 (WinError 5 або EACCES). "
            "YEBOYBot.cmd перевірить доступні журнали блокувань автоматично. "
            "Це може бути політика виконання програм або права доступу; "
            "не вимикайте захист Windows і не перевстановлюйте Python навмання."
        )
    return BootstrapError(hint, stage=stage, category=category,
                          exit_code=exit_code, errno=code, winerror=win)



def command_stage(args):
    if ENVIRONMENT_PROBE in args:
        return "environment-probe"
    if IMPORT_CHECK in args:
        return "library-import"
    if PIP_VERSION_CHECK in args:
        return "pip-version"
    if "ensurepip" in args:
        return "ensurepip"
    if "check" in args:
        return "dependency-check"
    if any(str(arg).startswith("pip==") for arg in args):
        return "pip-upgrade"
    return "dependency-install" if "install" in args else "subprocess"


def reported_winerror(output):
    """Extract an unambiguous code from Python's standard exception format."""
    codes = {int(code) for code in re.findall(r"\[WinError (\d{1,10})\]", output, re.IGNORECASE)}
    return next(iter(codes)) if len(codes) == 1 else None


def failure_hint(output):
    """Classify child failures without publishing the child's output."""
    value = output.lower()
    winerror = reported_winerror(output)
    if winerror is not None:
        error = OSError()
        error.winerror = winerror
        category, hint = os_failure_hint(error)
        if category != "filesystem":
            return category, hint
    for patterns, category, hint in (
        (("certificate_verify_failed", "sslcertverificationerror",
          "self signed certificate", "certificate verify failed",
          "unknown ca", "certificate chain"), "certificate",
         "Не підтверджено TLS-сертифікат. Перевірте дату Windows, довірені сертифікати та налаштування проксі; не вимикайте TLS-перевірку."),
        (("proxyerror", "proxy authentication required", "407 proxy authentication",
          "cannot connect to proxy", "proxy authentication failed"), "proxy",
         "Проксі відхилив або не встановив з'єднання. Перевірте його доступність та авторизацію без передавання адрес і паролів."),
        (("nameresolutionerror", "newconnectionerror", "getaddrinfo failed",
          "temporary failure in name resolution", "name or service not known",
          "failed to establish a new connection", "connectionerror",
          "connection refused", "name resolution", "network is unreachable",
          "timed out", "connecttimeouterror", "readtimeouterror",
          "winerror 10060", "winerror 10061", "winerror 11001"), "network",
         "Не вдалося отримати пакети через мережу. Перевірте DNS, HTTPS-доступ до pypi.org і files.pythonhosted.org, брандмауер та проксі."),
        (("sslerror", "tlsv1 alert", "tls handshake"), "tls",
         "Захищене TLS-з'єднання не встановлено. Перевірте Windows, сертифікати та мережевий фільтр; не вимикайте перевірку TLS."),
        (("no module named", "modulenotfounderror", "packagenotfounderror"), "missing-module",
         "Не знайдено потрібний модуль. Запустіть відновлення бібліотек після перевірки середовища."),
        (("permission denied", "access is denied"), "access-denied",
         "Немає доступу до файлів. Закрийте інші процеси бота й перевірте права та захист папки Windows."),
        (("no space left", "disk full"), "disk-full",
         "Недостатньо вільного місця на диску."),
        (("no matching distribution", "could not find a version"), "package-unavailable",
         "pip не отримав сумісний пакет. Це може означати недоступний індекс, фільтр мережі або обмеження версій; перевірте доступ до PyPI."),
        (("syntaxerror",), "probe-syntax", "Помилка запуску перевірки Python. Оновіть повний комплект файлів бота."),
    ):
        if any(pattern in value for pattern in patterns):
            return category, hint
    if re.match(r"^no python at ['\"]", value.lstrip()):
        return "broken-interpreter", (
            "Середовище посилається на відсутній Python. Потрібне відновлення .venv-v2 зі справного Python 3.12 x64.")
    return "unknown", "Причину не визначено автоматично. Передайте безпечний install-report.json для діагностики; не повторюйте встановлення навмання."

def write_failure_report(root, error, *, attempt_id=None):
    error = normalize_failure(error)
    path = Path(root) / "data/v2/install-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError("Report target is a symlink")
    report = {"status": "failed", "time_utc": datetime.now(UTC).isoformat(),
              "python_version": list(sys.version_info[:3]), "platform": sys.platform,
              "stage": error.stage if error.stage in STAGE_LABELS else "bootstrap",
              "category": error.category if error.category in CATEGORY_LABELS else "unknown",
              "exit_code": numeric_code(getattr(error, "exit_code", None)),
              "errno": numeric_code(getattr(error, "errno", None)),
              "winerror": numeric_code(getattr(error, "winerror", None))}
    if isinstance(attempt_id, str) and re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        report["attempt_id"] = attempt_id
    temporary = path.with_name("install-report-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass  # Preserve the original diagnostic/report failure.


def run(args, root):
    stage = command_stage(args)
    try:
        result = subprocess.run(args, check=False, cwd=root, env=clean_environment(), timeout=600,
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        raise command_failure(exc, stage) from exc
    if result.returncode:
        error = command_failure(subprocess.CalledProcessError(
            result.returncode, args, output=result.stdout, stderr=result.stderr), stage)
        if stage == "pip-upgrade" and error.category == "package-unavailable":
            error.args = (
                f"Фіксована версія pip {PIP_VERSION} існує на PyPI, але її не вдалося отримати з цього ПК. "
                "Перевірте https://pypi.org/simple/pip/ у браузері та доступ до "
                "files.pythonhosted.org; перевстановлення Python зазвичай не потрібне.",
            )
        raise error
    return result.stdout or ""


ENVIRONMENT_PROBE = r'''
import json, site, sys, sysconfig
from pathlib import Path
print(json.dumps(dict(prefix=sys.prefix, base=sys.base_prefix,
    version=list(sys.version_info[:2]), isolated=bool(sys.flags.isolated),
    user_site=site.ENABLE_USER_SITE, paths=sys.path,
    stdlib=[sysconfig.get_path('stdlib'), sysconfig.get_path('platstdlib'), str(Path(sys.base_prefix)),
        str(Path(sys.base_prefix) / 'DLLs'),
        str(Path(sys.base_prefix) / f'python{sys.version_info.major}{sys.version_info.minor}.zip'),
        str(Path(sysconfig.get_path('stdlib')).parent / f'python{sys.version_info.major}{sys.version_info.minor}.zip'),
        str(Path(sysconfig.get_path('stdlib')) / 'lib-dynload')])) )
'''


def environment_directory(root):
    target = Path(root) / ".venv-v2"
    if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
        raise BootstrapError(".venv-v2 не може бути посиланням. Збережіть стару папку перейменуванням і повторіть встановлення.")
    if target.exists() and not target.is_dir():
        raise BootstrapError("Шлях .venv-v2 зайнятий файлом; збережіть його й повторіть встановлення.")
    return target


def validate_environment(root):
    target = environment_directory(root).resolve()
    try:
        cfg = target / "pyvenv.cfg"
        if cfg.is_symlink() or not cfg.is_file():
            raise ValueError("Missing owned venv configuration")
        values = {key.strip().lower(): value.strip().lower() for key, value in
                      (line.split("=", 1) for line in cfg.read_text(encoding="utf-8").splitlines() if "=" in line)}
        if values.get("include-system-site-packages") != "false":
            raise ValueError("System site packages are not isolated")
        info = json.loads(run([str(python_path(root)), "-I", "-c", ENVIRONMENT_PROBE], root))
        if (Path(info["prefix"]).resolve() != target or
                Path(info["prefix"]).resolve() == Path(info["base"]).resolve() or
                info["version"] != [3, 12] or not info["isolated"] or info["user_site"]):
            raise ValueError("Wrong interpreter or prefix")
        allowed = {Path(path).resolve() for path in info["stdlib"]}
        for path in info["paths"]:
            resolved = Path(path).resolve()
            if not resolved.is_relative_to(target) and resolved not in allowed:
                raise ValueError("External import path")
            if {"site-packages", "dist-packages"}.intersection(resolved.parts) and not resolved.is_relative_to(target):
                raise ValueError("External installed packages")
    except OSError as exc:
        raise command_failure(exc, "environment-validation") from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise BootstrapError("Потрібен окремий ізольований Python 3.12 з .venv-v2 без system/user site-packages. Збережіть неповне середовище перейменуванням і повторіть встановлення.",
                             stage="environment-validation", category="invalid-environment") from exc


def ensure_pip(root, stamp):
    interpreter = str(python_path(root))
    try:
        version = run([interpreter, "-I", "-c", PIP_VERSION_CHECK], root).strip()
    except BootstrapError as error:
        if error.category != "missing-module":
            raise
        # Recover only this verified venv's missing pip from Python's local bundle.
        stamp.unlink(missing_ok=True)
        print("Початкове встановлення pip із комплекту Python (без завантаження)…")
        run([interpreter, "-I", "-m", "ensurepip", "--upgrade", "--default-pip"], root)
        version = run([interpreter, "-I", "-c", PIP_VERSION_CHECK], root).strip()
    if pip_is_current(version):
        return
    stamp.unlink(missing_ok=True)
    print("Оновлення pip у .venv-v2 перед встановленням залежностей…")
    run(pip_command(interpreter, "install", "--only-binary=:all:", "--upgrade", "pip==" + PIP_VERSION), root)
    if not pip_is_current(run([interpreter, "-I", "-c", PIP_VERSION_CHECK], root).strip()):
        raise BootstrapError("Версію виправленого pip не підтверджено. Залежності не встановлювалися; повторіть встановлення.")


def python_path(root):
    return root / ".venv-v2" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def create_environment(target):
    try:
        # EnvBuilder's built-in ensurepip combines file creation and execution,
        # inherits ambient settings and has no timeout. Probe first, then use
        # our isolated, bounded ensure_pip path for the local pip bundle.
        venv.EnvBuilder(with_pip=False, system_site_packages=False, clear=False).create(target)
    except (OSError, subprocess.SubprocessError) as exc:
        raise command_failure(exc, "venv-create") from exc


def prepare(root=ROOT, *, repair=False):
    if sys.version_info[:2] != (3, 12):
        raise BootstrapError("Потрібен Python 3.12: https://www.python.org/downloads/ . Інші версії ще не перевірені.")
    root = Path(root).resolve()
    # Serialize installation and exclude a running bot while pip may change libraries.
    with InstanceLock(root / "data/v2/install.lock"), InstanceLock(root / "data/v2/bot.lock"):
        interpreter = python_path(root)
        requirements = root / "core_v2/requirements.txt"
        stamp = root / ".venv-v2/yeboy-requirements.sha256"
        digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
        target = environment_directory(root)
        if stamp.is_symlink():
            raise BootstrapError("Маркер встановлення не може бути посиланням; файли не змінено.")
        if not interpreter.is_file():
            owner = target / ".yeboy-core-owner"
            if target.exists() and (owner.is_symlink() or not owner.is_file() or owner.read_text(encoding="utf-8") != str(root)):
                raise BootstrapError("Невідоме неповне .venv-v2. Збережіть папку перейменуванням і повторіть встановлення.")
            target.mkdir(exist_ok=True)
            owner.write_text(str(root), encoding="utf-8")
            print("Створення .venv-v2…")
            create_environment(target)
        print("Перевірка запуску Python та ізоляції .venv-v2…")
        try:
            validate_environment(root)
        except BootstrapError as error:
            owner = target / ".yeboy-core-owner"
            if (error.stage != "environment-probe"
                    or error.category not in REBUILDABLE_ENVIRONMENT_CATEGORIES
                    or owner.is_symlink() or not owner.is_file()
                    or owner.read_text(encoding="utf-8") != str(root)):
                raise
            # A copied/relocated or partially created Windows venv may contain
            # python.exe but cannot execute. Preserve it; rebuild once, not a loop.
            saved = root / (".venv-v2.failed-" + uuid.uuid4().hex)
            target.rename(saved)
            print("Непрацездатне власне середовище збережено в .venv-v2.failed-*; створюється нове.")
            target.mkdir()
            (target / ".yeboy-core-owner").write_text(str(root), encoding="utf-8")
            create_environment(target)
            validate_environment(root)
        install = repair or not stamp.is_file() or stamp.read_text(encoding="ascii").strip() != digest
        try:
            # Enforce the installer version even if application requirements are unchanged.
            ensure_pip(root, stamp)
            if not install:
                try:
                    run(pip_command(interpreter, "check"), root)
                    run([str(interpreter), "-I", "-c", IMPORT_CHECK], root)
                except BootstrapError as error:
                    if (error.stage not in {"dependency-check", "library-import"}
                            or error.category in NO_AUTOMATIC_REPAIR):
                        raise
                    stamp.unlink(missing_ok=True)
                    install = True
                    print("Перевірка бібліотек не пройшла; одна спроба відновлення залежностей…")
            if install:
                stamp.unlink(missing_ok=True)
                print("Встановлення залежностей у .venv-v2…")
                run(pip_command(interpreter, "install", "--only-binary=:all:", "-r", str(requirements)), root)
            if not pip_is_current(run([str(interpreter), "-I", "-c", PIP_VERSION_CHECK], root).strip()):
                raise BootstrapError("Після встановлення версію pip не підтверджено; повторіть встановлення.")
            run(pip_command(interpreter, "check"), root)
            run([str(interpreter), "-I", "-c", IMPORT_CHECK], root)
        except BootstrapError:
            try:
                stamp.unlink(missing_ok=True)
            except OSError:
                # A failed cleanup must not hide the original installation error.
                print("Не вдалося прибрати маркер перевірки; наступний запуск повторно перевірить бібліотеки.")
            raise
        stamp.write_text(digest + "\n", encoding="ascii")
        for folder in ("data/v2/logs", "backups"):
            (root / folder).mkdir(parents=True, exist_ok=True)
        # Exclusive creation preserves an existing token and custom settings.
        template = (root / ".env.v2.example").read_text(encoding="utf-8")
        try:
            with (root / ".env.v2").open("x", encoding="utf-8") as target:
                target.write(template)
        except FileExistsError:
            pass
    print("Середовище готове. Токен і готовність Discord перевіряються окремо через меню.")
    print("Git: " + ("доступний" if shutil.which("git") else "відсутній; Git-оновлення недоступне. https://git-scm.com/downloads/win"))
    return interpreter



def normalize_failure(error):
    """Only BootstrapError contains messages authored by this installer."""
    if isinstance(error, BootstrapError):
        return error
    if isinstance(error, OSError):
        return command_failure(error, "bootstrap")
    if isinstance(error.__cause__, OSError):
        return command_failure(error.__cause__, "bootstrap")
    return BootstrapError(
        "Не вдалося завершити підготовку. Перевірте, чи не працює бот або інше вікно встановлення. "
        "Якщо ні — передайте безпечний звіт; точна причина поки не підтверджена.")


def render_failure(error):
    error = normalize_failure(error)
    stage = error.stage if error.stage in STAGE_LABELS else "bootstrap"
    category = error.category if error.category in CATEGORY_LABELS else "unknown"
    codes = [f"{label}={value}" for label, value in (
        ("WinError", numeric_code(error.winerror)), ("errno", numeric_code(error.errno)),
        ("код процесу", numeric_code(error.exit_code))) if value is not None]
    return "\n".join([
        "ВСТАНОВЛЕННЯ ЗУПИНЕНО",
        f"Етап: {STAGE_LABELS[stage]} [{stage}]",
        f"Тип помилки: {CATEGORY_LABELS[category]} [{category}]",
        "Системні коди: " + (", ".join(codes) if codes else "не отримано"),
        "Пояснення та наступні дії:", str(error),
        "Для діагностики передайте новий install-report.json після цієї спроби.",
    ])

def main():
    try:
        prepare(repair="--repair" in sys.argv[1:])
    except (BootstrapError, OSError, RuntimeError) as exc:
        exc = normalize_failure(exc)
        print(render_failure(exc))
        try:
            write_failure_report(ROOT, exc, attempt_id=os.environ.get("YEBOY_BOOTSTRAP_ATTEMPT_ID"))
            print("Безпечний звіт: data/v2/install-report.json (без токена, URL і вмісту конфігурації).")
        except OSError:
            print("Не вдалося записати новий звіт; попередній файл може бути застарілим. Збережіть наведений вище блок помилки.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
