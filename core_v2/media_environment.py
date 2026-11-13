"""Offline voice prerequisites. No download, system install or Discord login."""
import importlib
import os
from pathlib import Path
import shutil

from .config import ROOT


def javascript_runtime(root=ROOT):
    """Prefer explicit DENO_PATH, then bundled tools, then a discovered runtime."""
    from dotenv import dotenv_values
    root = Path(root)
    # Settings.load() also loads this file. Reading only the selected key here
    # keeps the resolver usable in a standalone subprocess without a token.
    configured = os.getenv("DENO_PATH")
    if configured is None:
        configured = dotenv_values(root / ".env.v2").get("DENO_PATH")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = root / path
        if not path.is_file():
            raise ValueError("DENO_PATH не знайдено; повторіть вибір програм через початкове встановлення.")
        return {"deno": {"path": str(path.resolve())}}
    suffix = ".exe" if os.name == "nt" else ""
    for name in ("deno", "node"):
        paths = [root / "bin" / name / (name + suffix), root / "bin" / (name + suffix)]
        if os.name == "nt" and os.getenv("LOCALAPPDATA"):
            paths.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft/WinGet/Links" / (name + suffix))
        found = next((str(path.resolve()) for path in paths if path.is_file()), None) or shutil.which(name)
        if found:
            return {name: {"path": found}}
    raise ValueError("Для YouTube потрібен Deno або Node.js ≥20. Windows: [20] у головному меню; Linux: установіть підтримуваний runtime.")


def ffmpeg_path(configured="ffmpeg", root=ROOT):
    if configured and configured != "ffmpeg":
        path = Path(configured).expanduser()
        path = path if path.is_absolute() else root / path
        if path.is_file():
            return str(path.resolve())
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        raise ValueError("FFMPEG_PATH не знайдено; перевірте [25].")
    filename = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    paths = [root / "bin" / "ffmpeg" / filename, root / "bin" / filename]
    if os.name == "nt" and os.getenv("LOCALAPPDATA"):
        paths.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft/WinGet/Links/ffmpeg.exe")
    for path in paths:
        if path.is_file():
            return str(path.resolve())
    resolved = shutil.which("ffmpeg")
    if resolved:
        return resolved
    raise ValueError("FFmpeg не знайдено. Відкрийте [20] у YEBOYBot.cmd.")


def check_music(configured="ffmpeg", root=ROOT):
    for module in ("nacl", "davey", "yt_dlp", "yt_dlp_ejs"):
        try:
            importlib.import_module(module)
        except Exception:
            raise ValueError("Музичні бібліотеки недоступні; зупиніть бот і виберіть [5].") from None
    try:
        import discord.opus
        # The supported SDK lazily loads its platform Opus library here. No
        # voice connection or audio capture is started by creating an encoder.
        encoder = discord.opus.Encoder()
        del encoder
    except Exception:
        raise ValueError("Бібліотека Opus недоступна. Windows: [5]; Linux: установіть libopus штатним менеджером ОС.") from None
    javascript_runtime(root)
    return ffmpeg_path(configured, root)
