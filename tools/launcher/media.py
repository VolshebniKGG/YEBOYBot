"""Explicit music opt-in and official optional FFmpeg install from the CMD menu."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

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


def menu(root=ROOT):
    print("Музика Core V2: /music. Безпечний токен лишається в .env.v2 або Credentials/Token options.ini.")
    print("[1] Підготувати options.ini й увімкнути музику  [2] Вимкнути  [3] Встановити FFmpeg (Windows)  [4] Встановити Deno для YouTube  [0] Назад")
    choice = input("Вибір: ").strip()
    if choice == "3":
        install_ffmpeg(root)
    elif choice == "4":
        install_deno(root)
    elif choice == "1":
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
