"""Explicit music opt-in and official optional FFmpeg install from the CMD menu."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
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


def prepare_portable(root=ROOT):
    from tools.portable_media import ensure_tools
    print("  • Перевірка локальних FFmpeg і Deno…")
    ensure_tools(root)
    print("  • Підготовка налаштувань музики…")
    prepare_options(root)
    values = dotenv_values(root / ".env.v2")
    configured = os.getenv("FFMPEG_PATH", values.get("FFMPEG_PATH") or "ffmpeg")
    print("  • Перевірка голосових бібліотек і музичних програм…")
    check_music(configured, root)
    print("[OK] Музичний комплект готовий. Наявні налаштування збережено.")


def main(root=ROOT):
    from tools.portable_media import MediaInstallError
    try:
        prepare_portable(root)
    except MediaInstallError as error:
        print("[ПОМИЛКА] " + str(error))
        return 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile):
        print("[ПОМИЛКА] Музичний комплект не готовий. Перевірте інтернет, права папки та вільне місце; зупиніть бот перед повторенням.")
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
    raise SystemExit(main())
