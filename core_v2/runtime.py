import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets):
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.secrets = [s for s in secrets if s]

    def format(self, record):
        result = super().format(record)
        for secret in self.secrets:
            result = result.replace(secret, "[REDACTED]")
        return result


def configure_logging(settings):
    Path("data/v2/logs").mkdir(parents=True, exist_ok=True)
    formatter = RedactingFormatter([settings.token, settings.database])
    handlers = [logging.StreamHandler(), RotatingFileHandler(
        "data/v2/logs/bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")]
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)


class InstanceLock:
    """OS lock is released after crashes; the file itself is deliberately retained."""
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0)
        if self.file.read(1) == b"":
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            self.file = None
            raise RuntimeError("Інший процес Core v2 вже працює в цій папці.") from error
        return self

    def __exit__(self, *_):
        if self.file:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()
