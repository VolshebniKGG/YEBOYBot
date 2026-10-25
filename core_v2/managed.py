"""Single-process managed runtime: OS lock, instance nonce and cooperative stop."""
import asyncio
import json
import logging
import os
import time
import uuid

from .config import ROOT, Settings
from .runtime import InstanceLock, configure_logging
from .recovery import ControlFailure, recover

STATE = ROOT / "data/v2/managed.json"
STOP = ROOT / "data/v2/stop.json"


def write_state(path, value):
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(value), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as error:
        raise ControlFailure("Cannot write managed heartbeat state") from error
    finally:
        # Failed publication must not hide the original I/O error during cleanup.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def stop_requested(instance):
    try:
        return json.loads(STOP.read_text(encoding="utf-8")).get("instance") == instance
    except (OSError, ValueError):
        return False


async def control(bot, instance):
    while True:
        if stop_requested(instance):
            await bot.close()
            return
        write_state(STATE, {"instance": instance, "pid": os.getpid(),
                           "heartbeat": time.time(), "ready": bot.is_ready()})
        await asyncio.sleep(1)


async def run(settings, instance):
    from .bot import Bot
    async with Bot(settings) as bot:
        client = asyncio.create_task(bot.start(settings.token), name="discord-client")
        watcher = asyncio.create_task(control(bot, instance), name="managed-control")
        try:
            completed, _ = await asyncio.wait({client, watcher}, return_when=asyncio.FIRST_COMPLETED)
            # Await original tasks directly: preserve LoginFailure/other fatal types.
            # A failed control task must end the client, even when start() is waiting.
            if watcher in completed:
                await watcher
            if client in completed:
                await client
        finally:
            for task in (client, watcher):
                task.cancel()
            await asyncio.gather(client, watcher, return_exceptions=True)


def main():
    os.chdir(ROOT)
    settings = Settings.load()
    configure_logging(settings)
    with InstanceLock(ROOT / "data/v2/bot.lock"):
        instance = uuid.uuid4().hex
        code = 0
        try:
            def heartbeat(attempt, timestamp):
                write_state(STATE, {"instance": instance, "pid": os.getpid(),
                                   "heartbeat": timestamp, "ready": False,
                                   "recovering": attempt})
            asyncio.run(recover(lambda: run(settings, instance),
                                lambda: stop_requested(instance), heartbeat))
        except Exception:
            logging.getLogger(__name__).exception("Managed bot stopped")
            code = 1
        finally:
            try:
                STATE.unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).exception("Cannot remove managed state")
                code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
