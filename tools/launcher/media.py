"""Explicit music opt-in and official optional FFmpeg install from the CMD menu."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
import zipfile

from dotenv import dotenv_values, set_key

from core_v2.config import ROOT
from core_v2.media_environment import check_music, ffmpeg_path
from core_v2.runtime import InstanceLock


def prepare_options(root=ROOT):
    """Copy the original commented template exactly once, never overwrite secrets."""
    path = root / "config/options.ini"
    template = (root / "config/example_options.ini").read_text(encoding="utf-8")
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(template)
    except FileExistsError:
        pass
    return path


def save_enabled(root, enabled):
    with InstanceLock(root / "data/v2/config.lock"):
        env = root / ".env.v2"
        original = env.read_text(encoding="utf-8") if env.exists() else (root / ".env.v2.example").read_text(encoding="utf-8")
        fd, name = tempfile.mkstemp(prefix=".env.v2.", suffix=".tmp", dir=root)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(original)
            set_key(str(temporary), "ENABLE_MUSIC", "true" if enabled else "false")
            os.replace(temporary, env)
        finally:
            temporary.unlink(missing_ok=True)


def install_ffmpeg(root=ROOT):
    return _install_native(root, "Gyan.FFmpeg", "FFMPEG", lambda: ffmpeg_path(root=root))


def install_deno(root=ROOT):
    from core_v2.media_environment import javascript_runtime
    return _install_native(root, "DenoLand.Deno", "DENO", lambda: javascript_runtime(root))


def _install_native(root, package, label, verify):
    if os.name != "nt":
        print(f"Встановіть {label} пакетним менеджером Linux; див. docs/RANK_MUSIC_UA.md.")
        return False
    winget = shutil.which("winget")
    if not winget:
        print("Потрібен офіційний Windows App Installer: https://apps.microsoft.com/detail/9nblggh4nns1")
        return False
    print(f"Буде встановлено {package} через офіційний каталог WinGet для поточного користувача.")
    if input(f"Для погодження встановлення й умов пакета введіть ВСТАНОВИТИ {label}: ").strip() != f"ВСТАНОВИТИ {label}":
        return False
    try:
        result = subprocess.run([winget, "install", "--id", package, "--exact", "--source", "winget",
                                 "--scope", "user", "--architecture", "x64", "--silent",
                                 "--accept-package-agreements", "--accept-source-agreements",
                                 "--disable-interactivity"], cwd=root, timeout=600,
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode:
            print("WinGet не підтвердив встановлення. Перевірте доступність пакета й політику Windows.")
            return False
        verify()
        print(f"{label} знайдено. Перезапустіть меню, якщо PATH ще не оновився.")
        return True
    except (OSError, subprocess.SubprocessError, ValueError):
        print(f"{label} не підтверджено. Перевірте встановлення та PATH/FFMPEG_PATH.")
        return False


def check_enabled_music(root=ROOT):
    """Offline readiness; do not load token/settings or mutate the environment."""
    values = dotenv_values(root / ".env.v2")
    enabled = os.getenv("ENABLE_MUSIC", values.get("ENABLE_MUSIC") or "false")
    enabled = enabled.strip().lower()
    if enabled not in {"true", "false"}:
        raise ValueError("ENABLE_MUSIC: use true or false")
    if enabled == "true":
        configured = os.getenv("FFMPEG_PATH", values.get("FFMPEG_PATH") or "ffmpeg")
        check_music(configured, root)


def _active_system_tools(root, selection):
    """Verify the selected external executables again, immediately before use."""
    from tools.launcher.system_media import validate
    from tools.portable_media import MediaInstallError
    root = Path(root).resolve()
    values = dotenv_values(root / ".env.v2") if selection is None else {}
    selected = {}
    for name, key in (("ffmpeg", "FFMPEG_PATH"), ("deno", "DENO_PATH")):
        configured = (selection.get(name) or "") if selection is not None else (
            os.getenv(key, values.get(key) or ""))
        if not configured or configured == name:
            continue
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = root / path
        if path.resolve().is_relative_to(root):
            continue  # A local choice; the signed installer handles this tool.
        checked = validate(name, path, root)
        if not checked["compatible"]:
            raise MediaInstallError(
                f"{name}: зовнішня програма не пройшла перевірку версії або запуску. "
                "Виберіть локальне встановлення через YEBOYBot.cmd.")
        selected[name] = checked["path"]
    return selected


def save_runtime_paths(root, ffmpeg, deno):
    """Commit both explicit executable choices atomically; retain all secrets."""
    root = Path(root)
    env = root / ".env.v2"
    if env.is_symlink():
        raise ValueError("Конфігурація не може бути символічним посиланням.")
    with InstanceLock(root / "data/v2/bot.lock"), InstanceLock(root / "data/v2/config.lock"):
        original = env.read_text(encoding="utf-8") if env.exists() else (
            root / ".env.v2.example").read_text(encoding="utf-8")
        fd, name = tempfile.mkstemp(prefix=".env.v2.", suffix=".tmp", dir=root)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(original)
            set_key(str(temporary), "FFMPEG_PATH", ffmpeg)
            set_key(str(temporary), "DENO_PATH", deno)
            os.replace(temporary, env)
        finally:
            temporary.unlink(missing_ok=True)


def prepare_portable(root=ROOT, *, selection=None):
    from tools.portable_media import ensure_tools
    root = Path(root).resolve()
    external = _active_system_tools(root, selection)
    local = tuple(name for name in ("ffmpeg", "deno") if name not in external)
    for name in external:
        print(f"  [OK] Зовнішній {name}: {external[name]} (повторне завантаження не потрібне).")
    if local:
        print("  • Перевірка локальних: " + ", ".join(local) + "…")
        if len(local) == 2:
            ensure_tools(root)
        else:
            ensure_tools(root, names=local)
    else:
        print("  [OK] Обидві програми використовуються з ПК; локальні архіви не завантажуються.")
    # The env file is written only after libraries and BOTH media tools passed
    # readiness. Do not modify a pre-existing token or preferences on failure.
    ffmpeg = external.get("ffmpeg", "bin/ffmpeg/ffmpeg.exe")
    deno = external.get("deno", "bin/deno/deno.exe")
    previous_deno = os.environ.get("DENO_PATH")
    try:
        os.environ["DENO_PATH"] = deno
        print("  • Перевірка голосових бібліотек і вибраних музичних програм…")
        check_music(ffmpeg, root)
    finally:
        if previous_deno is None:
            os.environ.pop("DENO_PATH", None)
        else:
            os.environ["DENO_PATH"] = previous_deno
    prepare_options(root)
    if selection is not None:
        save_runtime_paths(root, ffmpeg, deno)
        if os.getenv("FFMPEG_PATH") or os.getenv("DENO_PATH"):
            print("[!] Змінні середовища FFMPEG_PATH/DENO_PATH мають пріоритет над .env.v2.")
    print("[OK] Музичний комплект готовий. Конфігурація й дані бота збережені.")


def main(root=ROOT, *, args=()):
    from tools.portable_media import MediaInstallError
    parser = argparse.ArgumentParser(description="FFmpeg і Deno для YEBOYBot")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--select-media", action="store_true")
    parser.add_argument("--system-ffmpeg", default="")
    parser.add_argument("--system-deno", default="")
    options = parser.parse_args(args)
    if (options.system_ffmpeg or options.system_deno) and not options.select_media:
        parser.error("system media paths require --select-media")
    selection = ({"ffmpeg": options.system_ffmpeg, "deno": options.system_deno}
                 if options.select_media else None)
    try:
        if selection is None:
            prepare_portable(root)
        else:
            prepare_portable(root, selection=selection)
    except MediaInstallError as error:
        print("[ПОМИЛКА] " + str(error))
        return 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile):
        print("[ПОМИЛКА] Музичний комплект не готовий. Перевірте доступ до програм, права папки та мережу; зупиніть бот перед повторенням.")
        return 1
    return 0


def menu(root=ROOT):
    print("Музика Core V2: /music. Безпечний токен лишається в .env.v2 або Credentials/Token options.ini.")
    print("\n[0] Назад\n[1] Автоматично підготувати все й увімкнути музику\n[2] Вимкнути музику\n[3] FFmpeg через WinGet (альтернатива)\n[4] Deno через WinGet (альтернатива)")
    choice = input("Вибір: ").strip()
    if choice == "3":
        install_ffmpeg(root)
    elif choice == "4":
        install_deno(root)
    elif choice == "1":
        if os.name == "nt" and main(root):
            return
        values = dotenv_values(root / ".env.v2")
        configured = os.getenv("FFMPEG_PATH", values.get("FFMPEG_PATH") or "ffmpeg")
        check_music(configured, root)
        prepare_options(root)
        from core_v2.music_options import load_music_options
        load_music_options(root)
        save_enabled(root, True)
        print("Увімкнено. options.ini: Chat/AutojoinChannels і MusicBot. [11] синхронізуйте команди, [2] перезапустіть.")
    elif choice == "2":
        save_enabled(root, False)
        print("Музику вимкнено в конфігурації. Перезапустіть бот через [2].")
    if os.getenv("ENABLE_MUSIC"):
        print("Змінна середовища ENABLE_MUSIC має пріоритет над файлом.")


if __name__ == "__main__":
    raise SystemExit(main(args=sys.argv[1:]))
