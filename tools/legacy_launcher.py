"""Stdlib-only controller; legacy packages live exclusively in .venv-legacy."""
import argparse
import asyncio
import configparser
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import site
import subprocess
import sys
import sysconfig
import venv

ROOT = Path(__file__).resolve().parents[1]
# -I excludes the script directory. Add only the trusted checkout, not cwd.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
InstanceLock = importlib.import_module("core_v2.runtime").InstanceLock


class LegacyError(RuntimeError):
    pass


def configure_console():
    # -I ignores PYTHONUTF8, including when stdout is redirected by Windows CI.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def legacy_python(root):
    return Path(root) / ".venv-legacy" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def clean_environment(source=None):
    env = dict(os.environ if source is None else source)
    for key in list(env):
        if key.upper().startswith(("PYTHON", "PIP_")) or key.upper() in {"VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT"}:
            del env[key]
    env.update(PYTHONUTF8="1", PIP_CONFIG_FILE=os.devnull)
    return env


@contextmanager
def locked(root, name):
    lock = InstanceLock(Path(root) / "data/legacy" / (name + ".lock"))
    try:
        lock.__enter__()
    except RuntimeError as exc:
        raise LegacyError("Інша legacy-операція вже працює. Закрийте її та повторіть.") from exc
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def _layout(root):
    root = Path(root).resolve()
    target = root / ".venv-legacy"
    if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
        raise LegacyError(".venv-legacy не може бути посиланням на інше середовище.")
    if target.exists() and not target.is_dir():
        raise LegacyError("Шлях .venv-legacy зайнятий файлом.")
    cfg = target / "pyvenv.cfg"
    if cfg.is_file():
        values = dict((key.strip().lower(), value.strip().lower()) for key, value in
                      (line.split("=", 1) for line in cfg.read_text(encoding="utf-8").splitlines() if "=" in line))
        if values.get("include-system-site-packages") != "false":
            raise LegacyError("Legacy-середовище повинно мати include-system-site-packages = false.")
    core = root / ".venv-v2"
    if target.exists() and core.exists() and os.path.samefile(target, core):
        raise LegacyError("Legacy і Core V2 повинні мати окремі середовища.")
    return target


PROBE = r'''
import importlib.metadata as md, json, site, sys, sysconfig
from pathlib import Path
names = ('discord.py', 'py-cord', 'Pillow', 'spotipy', 'yt-dlp', 'ConfigUpdater')
versions = {}
for name in names:
    try: versions[name] = md.version(name)
    except md.PackageNotFoundError: pass
print(json.dumps(dict(prefix=sys.prefix, base=sys.base_prefix,
    version=list(sys.version_info[:2]), isolated=bool(sys.flags.isolated),
    user_site=site.ENABLE_USER_SITE, paths=sys.path, packages=versions,
    stdlib=[sysconfig.get_path('stdlib'), sysconfig.get_path('platstdlib'), str(Path(sys.base_prefix)),
        str(Path(sys.base_prefix) / 'DLLs'),
        str(Path(sys.base_prefix) / f'python{sys.version_info.major}{sys.version_info.minor}.zip'),
        str(Path(sysconfig.get_path('stdlib')).parent / f'python{sys.version_info.major}{sys.version_info.minor}.zip'),
        str(Path(sysconfig.get_path('stdlib')) / 'lib-dynload')])) )
'''
IMPORTS = ("import discord.ext.commands, aiohttp, PIL, spotipy, yt_dlp, configupdater; "
           "import inspect; assert inspect.iscoroutinefunction(discord.ext.commands.Bot.add_cog)")


def _checked(root, args, *, timeout=120):
    try:
        result = subprocess.run(args, cwd=Path(root), env=clean_environment(),
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise LegacyError("Не вдалося виконати legacy-операцію. Перевірте Python, права й мережу.") from exc
    if result.returncode:
        # Package managers and config tracebacks may include credentials/URLs.
        raise LegacyError("Legacy-операція завершилася помилкою. Перевірте мережу, сертифікати та сумісність залежностей; повторіть [2].")
    return result.stdout


def expected_versions(root):
    result = {}
    for line in (Path(root) / "requirements-legacy.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([A-Za-z0-9_.+-]+)", line.strip())
        if not match:
            raise LegacyError("Legacy requirements повинні містити лише перевірені точні версії пакетів.")
        result[match[1].lower().replace("_", "-")] = match[2]
    return result


def _verify(root, info, require_packages):
    target = _layout(root).resolve()
    if (Path(info["prefix"]).resolve() != target or info["prefix"] == info["base"]
            or info["version"] != [3, 12] or not info["isolated"] or info["user_site"]):
        raise LegacyError("Потрібен ізольований Python 3.12 саме з .venv-legacy.")
    allowed = {Path(path).resolve() for path in info["stdlib"]}
    allowed.add(Path(root).resolve())
    for path in info["paths"]:
        resolved = Path(path).resolve()
        if {"site-packages", "dist-packages"}.intersection(resolved.parts) and not resolved.is_relative_to(target):
            raise LegacyError("У legacy-середовище підключено сторонні пакети. Створіть окреме середовище без system-site-packages.")
        if not resolved.is_relative_to(target) and resolved not in allowed:
            raise LegacyError("Виявлено сторонній шлях імпорту legacy. Перевірте .pth у .venv-legacy або створіть чисте окреме середовище.")
    if "py-cord" in info["packages"]:
        raise LegacyError("Виявлено py-cord у legacy-середовищі. Перейменуйте .venv-legacy і повторіть встановлення; Core V2 не змінюйте.")
    if require_packages and not {"discord.py", "Pillow", "spotipy", "yt-dlp", "ConfigUpdater"} <= info["packages"].keys():
        raise LegacyError("Залежності legacy неповні. Виконайте [2] у legacy-меню.")
    if require_packages:
        actual = {name.lower().replace("_", "-"): version for name, version in info["packages"].items()}
        if any(actual.get(name) != version for name, version in expected_versions(root).items()):
            raise LegacyError("Версії legacy-залежностей відрізняються від перевірених. Виконайте [2] у legacy-меню.")
    return info


def validate_environment(root, require_packages=False):
    _layout(root)
    try:
        info = json.loads(_checked(root, [str(legacy_python(root)), "-I", "-c", PROBE]))
        _verify(root, info, require_packages)
    except (ValueError, KeyError, TypeError) as exc:
        raise LegacyError("Некоректна відповідь legacy-інтерпретатора.") from exc
    if require_packages:
        _checked(root, [str(legacy_python(root)), "-I", "-c", IMPORTS])
    return info


def _prepare(root, repair=False):
    root = Path(root).resolve()
    if sys.version_info[:2] != (3, 12):
        raise LegacyError("Контролер потребує Python 3.12.")
    target = _layout(root)
    owner = target / ".yeboy-legacy-owner"
    if not legacy_python(root).exists():
        if target.exists() and (not owner.is_file() or owner.read_text(encoding="utf-8") != str(root)):
            raise LegacyError("Неповне невідоме .venv-legacy. Перейменуйте його для збереження даних і повторіть.")
        target.mkdir(exist_ok=True)
        owner.write_text(str(root), encoding="utf-8")
        venv.EnvBuilder(with_pip=True, system_site_packages=False, clear=False).create(target)
    validate_environment(root)
    digest = hashlib.sha256((root / "requirements-legacy.txt").read_bytes()).hexdigest()
    stamp = target / ".yeboy-legacy-requirements.sha256"
    if not repair and stamp.exists() and stamp.read_text(encoding="ascii") == digest:
        validate_environment(root, require_packages=True)
        return
    # No global/user installs, cache deletion, uninstall, or system config changes.
    # Inherited CA trust is explicit because --isolated ignores PIP_CERT.
    args = [str(legacy_python(root)), "-I", "-m", "pip", "--isolated",
            "--require-virtualenv", "--disable-pip-version-check"]
    cert = os.environ.get("PIP_CERT") or os.environ.get("REQUESTS_CA_BUNDLE")
    if cert:
        args += ["--cert", cert]
    _checked(root, args + ["install", "--no-cache-dir", "--only-binary=:all:",
                          "-r", str(root / "requirements-legacy.txt")], timeout=900)
    validate_environment(root, require_packages=True)
    _checked(root, [str(legacy_python(root)), "-I", "-m", "pip", "--isolated", "check"])
    stamp.write_text(digest, encoding="ascii")


def prepare(root, repair=False):
    with locked(root, "setup"), locked(root, "bot"):
        _prepare(root, repair)


def preflight(root, music=False):
    config = configparser.ConfigParser(interpolation=None)
    try:
        with (Path(root) / "config/options.ini").open(encoding="utf-8-sig") as stream:
            config.read_file(stream)
        token = config.get("Credentials", "Token", fallback="").strip()
        if not token or token.lower() in {"bot_token", "your_token", "your_bot_token", "token"}:
            raise LegacyError("Заповніть Token у config/options.ini. Токен не вводиться в командному рядку.")
        if music:
            binaries = [Path(r"E:\Discord Bot\Bot\bin") / name for name in ("ffmpeg.exe", "ffprobe.exe")]
            if not all(path.is_file() for path in binaries):
                raise LegacyError("Стара музика потребує FFmpeg/ffprobe за legacy-шляхом E:\\Discord Bot\\Bot\\bin. Оберіть [1] без музики.")
            if not all(config.get("Credentials", key, fallback="").strip() for key in ("Spotify_ClientID", "Spotify_ClientSecret")):
                raise LegacyError("Стара музика потребує обох Spotify credentials у config/options.ini. Оберіть [1] без музики.")
    except (OSError, configparser.Error, UnicodeError) as exc:
        raise LegacyError("Не вдалося прочитати config/options.ini. Скопіюйте config/example_options.ini в options.ini й налаштуйте токен.") from exc


def start(root, music=False):
    with locked(root, "launcher"):
        preflight(root, music)
        prepare(root)
        args = [str(legacy_python(root)), "-I", str(Path(root) / "run.py")]
        if music:
            args.append("--music")
        return subprocess.call(args, cwd=root, env=clean_environment())


def run_bot(root, music=False):
    packages = {}
    for name in ("discord.py", "py-cord", "Pillow", "spotipy", "yt-dlp", "ConfigUpdater"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    _verify(root, dict(prefix=sys.prefix, base=sys.base_prefix, version=list(sys.version_info[:2]),
                       isolated=bool(sys.flags.isolated), user_site=site.ENABLE_USER_SITE,
                       paths=sys.path, packages=packages,
                       stdlib=[sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib"), str(Path(sys.base_prefix)),
                               str(Path(sys.base_prefix) / "DLLs"),
                               str(Path(sys.base_prefix) / f"python{sys.version_info.major}{sys.version_info.minor}.zip"),
                               str(Path(sysconfig.get_path("stdlib")).parent / f"python{sys.version_info.major}{sys.version_info.minor}.zip"),
                               str(Path(sysconfig.get_path("stdlib")) / "lib-dynload")]), require_packages=True)
    preflight(root, music)
    with locked(root, "bot"):
        os.chdir(root)
        from yeboybot.bot import main as legacy_main
        asyncio.run(legacy_main(enable_music=music))
    return 0


def bot_entry(root=ROOT, music=False):
    configure_console()
    try:
        return run_bot(root, music)
    except KeyboardInterrupt:
        print("Legacy-бот зупинено користувачем.")
        return 130
    except Exception as exc:
        # Do not print untrusted Discord/config/downloader exception payloads.
        print(str(exc) if isinstance(exc, LegacyError) else "Legacy-бот завершився з помилкою. Перевірте очищений журнал logs/legacy/.")
        return 1


def main(argv=None):
    configure_console()
    parser = argparse.ArgumentParser(description="Ізольований legacy-запуск YEBOYBot")
    parser.add_argument("--install", action="store_true", help="встановити лише .venv-legacy; не запускати Discord")
    parser.add_argument("--check", action="store_true", help="перевірити встановлене середовище без запуску Discord")
    args = parser.parse_args(argv)
    try:
        if args.install:
            prepare(ROOT)
            print("Legacy-залежності перевірено в .venv-legacy. Core V2 не змінено.")
            return 0
        if args.check:
            info = validate_environment(ROOT, require_packages=True)
            print("Python:", ".".join(map(str, info["version"])), "| legacy-залежності: перевірено | Discord: не перевірено")
            return 0
        while True:
            print("\n=== YEBOYBot legacy (застарілий режим) ===\nОсновний запуск Core V2: YEBOYBot.cmd")
            print("[1] Старі команди без музики\n[2] Встановити / відновити legacy-залежності\n[3] Перевірити legacy-середовище\n[4] Старі команди з музикою (потрібні FFmpeg і Spotify)\n[0] Вихід")
            choice = input("Виберіть дію: ").strip()
            if choice == "0":
                return 0
            try:
                if choice in {"1", "4"}:
                    code = start(ROOT, music=choice == "4")
                    print("Legacy-процес завершено. Код:", code)
                elif choice == "2":
                    prepare(ROOT, repair=True)
                    print("Legacy-залежності перевірено.")
                elif choice == "3":
                    validate_environment(ROOT, require_packages=True)
                    print("Середовище перевірено. Discord API не перевірявся.")
                else:
                    print("Невідома дія.")
            except LegacyError as exc:
                print(str(exc))
    except (KeyboardInterrupt, EOFError):
        return 130
    except LegacyError as exc:
        print(str(exc))
        return 1
    except Exception:
        print("Не вдалося завершити legacy-операцію. Перевірте права доступу, Python 3.12 і вільне місце.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
