"""Offline voice prerequisites. No download, system install or Discord login."""
import importlib
import os
from pathlib import Path
import shutil

from .config import ROOT


def javascript_runtime(root=ROOT):
    """Use an already installed trusted runtime; never fetch executable code."""
    suffix = ".exe" if os.name == "nt" else ""
    for name in ("deno", "node"):
        paths = [root / "bin" / (name + suffix)]
        if os.name == "nt" and os.getenv("LOCALAPPDATA"):
            paths.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft/WinGet/Links" / (name + suffix))
        found = next((str(path.resolve()) for path in paths if path.is_file()), None) or shutil.which(name)
        if found:
            return {name: {"path": found}}
    raise ValueError("Для YouTube потрібен Deno або Node.js ≥20. Windows: [25] → [4] Deno; Linux: установіть підтримуваний runtime.")


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
    paths = [root / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")]
    if os.name == "nt" and os.getenv("LOCALAPPDATA"):
        paths.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft/WinGet/Links/ffmpeg.exe")
    for path in paths:
        if path.is_file():
            return str(path.resolve())
    resolved = shutil.which("ffmpeg")
    if resolved:
        return resolved
    raise ValueError("FFmpeg не знайдено. Відкрийте [25] у YEBOYBot.cmd.")


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
