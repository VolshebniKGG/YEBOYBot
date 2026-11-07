"""Repeatable Windows installation using only the standard library initially."""
import hashlib
import json
import os
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


class BootstrapError(RuntimeError):
    def __init__(self, message, *, stage="bootstrap", category="unknown", exit_code=None):
        super().__init__(message)
        self.stage, self.category, self.exit_code = stage, category, exit_code


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


def failure_hint(output):
    # Recognize fixed patterns only. Never echo command output, URLs, credentials,
    # environment values or arbitrary package names from a failed subprocess.
    value = output.lower()
    for patterns, category, hint in (
        (("certificate_verify_failed", "sslcertverificationerror"), "certificate",
         "Не підтверджено TLS-сертифікат. Перевірте дату Windows і довірені сертифікати; не вимикайте TLS-перевірку."),
        (("no matching distribution", "could not find a version"), "package-unavailable",
         "Не знайдено сумісного пакета. Перевірте доступ до PyPI та Python 3.12 x64; повторна інсталяція Python може не допомогти."),
        (("no python at", "unable to create process"), "broken-interpreter",
         "Середовище посилається на відсутній Python. Потрібне відновлення .venv-v2 зі справного Python 3.12 x64."),
        (("no module named", "modulenotfounderror"), "missing-module",
         "Не знайдено потрібний модуль. Запустіть відновлення бібліотек після перевірки середовища."),
        (("permission denied", "access is denied", "winerror 5"), "access-denied",
         "Немає доступу до файлів. Закрийте інші процеси бота й перевірте права та захист папки Windows."),
        (("no space left", "disk full", "winerror 112"), "disk-full",
         "Недостатньо вільного місця на диску."),
        (("proxyerror", "connectionerror", "connection refused", "name resolution", "timed out"), "network",
         "Не вдалося з'єднатися з джерелом пакетів. Перевірте інтернет, проксі та правила мережі."),
        (("syntaxerror",), "probe-syntax", "Помилка запуску перевірки Python. Оновіть повний комплект файлів бота."),
    ):
        if any(pattern in value for pattern in patterns):
            return category, hint
    return "unknown", "Причину не визначено автоматично. Передайте безпечний install-report.json для діагностики; не повторюйте встановлення навмання."


def write_failure_report(root, error):
    path = Path(root) / "data/v2/install-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError("Report target is a symlink")
    report = {"status": "failed", "time_utc": datetime.now(UTC).isoformat(),
              "python_version": list(sys.version_info[:3]), "platform": sys.platform,
              "stage": getattr(error, "stage", "bootstrap"),
              "category": getattr(error, "category", "filesystem"),
              "exit_code": getattr(error, "exit_code", None)}
    temporary = path.with_name("install-report-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(args, root):
    stage = command_stage(args)
    try:
        result = subprocess.run(args, check=False, cwd=root, env=clean_environment(), timeout=600,
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        category = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "process-start"
        raise BootstrapError(f"Етап {stage}: процес не запущено або перевищено час очікування ({category}).",
                             stage=stage, category=category) from exc
    if result.returncode:
        category, hint = failure_hint((result.stdout or "") + "\n" + (result.stderr or ""))
        raise BootstrapError(f"Етап {stage}, код {result.returncode}, категорія {category}.\n{hint}",
                             stage=stage, category=category, exit_code=result.returncode)
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
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise BootstrapError("Потрібен окремий ізольований Python 3.12 з .venv-v2 без system/user site-packages. Збережіть неповне середовище перейменуванням і повторіть встановлення.") from exc


def ensure_pip(root, stamp):
    interpreter = str(python_path(root))
    try:
        version = run([interpreter, "-I", "-c", PIP_VERSION_CHECK], root).strip()
    except BootstrapError:
        # Recover only this verified venv's missing pip from Python's local bundle.
        stamp.unlink(missing_ok=True)
        run([interpreter, "-I", "-m", "ensurepip", "--upgrade"], root)
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
        venv.EnvBuilder(with_pip=True, system_site_packages=False, clear=False).create(target)
    except (OSError, subprocess.SubprocessError) as exc:
        output = str(getattr(exc, "output", "") or "") + str(getattr(exc, "stderr", "") or "")
        category, hint = failure_hint(output)
        raise BootstrapError("Не вдалося створити .venv-v2. " + hint,
                             stage="venv-create", category=category,
                             exit_code=getattr(exc, "returncode", None)) from exc


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
        try:
            validate_environment(root)
        except BootstrapError as error:
            owner = target / ".yeboy-core-owner"
            if (error.stage != "environment-probe"
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
                    if error.stage not in {"dependency-check", "library-import"}:
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
            stamp.unlink(missing_ok=True)
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


def main():
    try:
        prepare(repair="--repair" in sys.argv[1:])
    except (BootstrapError, OSError, RuntimeError) as exc:
        print(f"Встановлення не завершено: {type(exc).__name__}")
        if isinstance(exc, (BootstrapError, RuntimeError)):
            print(str(exc))
        else:
            print("Перевірте права запису, вільне місце та доступність Python. Дані й конфігурація не видаляються.")
        try:
            write_failure_report(ROOT, exc)
            print("Безпечний звіт: data/v2/install-report.json (без токена, URL і вмісту конфігурації).")
        except OSError:
            print("Не вдалося записати звіт. Збережіть зазначені вище етап і код помилки.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
