"""Single-process managed runtime: OS lock, instance nonce and cooperative stop."""
import asyncio
import json
import logging
import os
import time
import uuid

from .config import ROOT, Settings
from .runtime import InstanceLock, configure_logging

STATE = ROOT / "data/v2/managed.json"
STOP = ROOT / "data/v2/stop.json"


def write_state(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


async def control(bot, instance):
    while True:
        write_state(STATE, {"instance": instance, "pid": os.getpid(),
                           "heartbeat": time.time(), "ready": bot.is_ready()})
        try:
            requested = json.loads(STOP.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            requested = {}
        if requested.get("instance") == instance:
            await bot.close()
            return
        await asyncio.sleep(1)


async def run(settings, instance):
    from .bot import Bot
    async with Bot(settings) as bot:
        watcher = asyncio.create_task(control(bot, instance))
        try:
            await bot.start(settings.token)
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)


def main():
    os.chdir(ROOT)
    settings = Settings.load()
    configure_logging(settings)
    with InstanceLock(ROOT / "data/v2/bot.lock"):
        instance = uuid.uuid4().hex
        try:
            asyncio.run(run(settings, instance))
        except Exception:
            logging.getLogger(__name__).exception("Managed bot stopped")
            return 1
        finally:
            STATE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
