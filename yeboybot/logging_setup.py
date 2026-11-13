"""Append-only, secret-aware logging for the separate legacy process."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable


DEFAULT_LOG_DIR = Path(__file__).resolve().parents[1] / "logs" / "legacy"
_handlers: list[logging.Handler] = []
_log_dir: Path | None = None
_secrets: set[str] = set()


class RedactingFormatter(logging.Formatter):
    """Redact the final text so exception messages and tracebacks are covered."""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for secret in sorted(_secrets, key=len, reverse=True):
            rendered = rendered.replace(secret, "[ПРИХОВАНО]")
        return rendered


def refresh_logging() -> None:
    """Detach unsafe handlers added by old cogs and route their logs safely.

    This only acts after setup_logging has installed the legacy handlers. Foreign
    handlers are detached rather than closed: they may belong to an embedding
    caller. Only handlers created here are owned and closed here.
    """
    if not _handlers:
        return
    root = logging.getLogger()
    loggers = [root, *(item for item in logging.root.manager.loggerDict.values()
                       if isinstance(item, logging.Logger))]
    for logger in loggers:
        for handler in logger.handlers[:]:
            if handler not in _handlers or logger is not root:
                logger.removeHandler(handler)
        if logger is not root:
            logger.propagate = True
    for handler in _handlers:
        if handler not in root.handlers:
            root.addHandler(handler)


def setup_logging(
    log_dir: str | Path | None = None, *, secrets: Iterable[str] = ()
) -> logging.Logger:
    """Append to logs/legacy and protect bot, third-party and exception output.

    Repeated calls reuse handlers. Credentials remain redacted until the owned
    handlers are closed, including when old cogs add their own handlers.
    """
    global _log_dir
    directory = Path(log_dir).resolve() if log_dir is not None else (_log_dir or DEFAULT_LOG_DIR)
    # Keep earlier credentials protected if configuration is refreshed in-process.
    protected = _secrets | {value for value in secrets if isinstance(value, str) and value}
    if _handlers and directory != _log_dir:
        close_logging()
    _secrets.update(protected)
    if not _handlers:
        directory.mkdir(parents=True, exist_ok=True)
        formatter = RedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        opened: list[logging.Handler] = []
        try:
            for filename, level in (("bot.log", logging.INFO), ("errors.log", logging.ERROR)):
                handler = logging.FileHandler(directory / filename, mode="a", encoding="utf-8")
                handler.setLevel(level)
                handler.setFormatter(formatter)
                opened.append(handler)
            console = logging.StreamHandler()
            console.setLevel(logging.INFO)
            console.setFormatter(formatter)
            opened.append(console)
        except BaseException:
            for handler in opened:
                handler.close()
            raise
        _handlers.extend(opened)
        _log_dir = directory
    logging.getLogger().setLevel(logging.INFO)
    logger = logging.getLogger("bot")
    logger.setLevel(logging.INFO)
    refresh_logging()
    return logger


def close_logging() -> None:
    """Flush and close only the file/stream handlers owned by this module."""
    global _log_dir
    root = logging.getLogger()
    for handler in _handlers:
        root.removeHandler(handler)
        handler.close()
    _handlers.clear()
    _secrets.clear()
    _log_dir = None
