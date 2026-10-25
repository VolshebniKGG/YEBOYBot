"""Bounded application recovery; Discord's own reconnect remains enabled."""
import asyncio
import logging
import time

from discord import LoginFailure, PrivilegedIntentsRequired

log = logging.getLogger(__name__)


class ControlFailure(RuntimeError):
    """The runtime cannot safely expose heartbeat/control state; stop retries."""


async def recover(operation, stopped, heartbeat, delays=(2, 5, 15), sleep=asyncio.sleep):
    """Never overlap clients; stop requests interrupt backoff within one second."""
    for attempt in range(len(delays) + 1):
        if stopped():
            return
        try:
            await operation()
            return
        except (LoginFailure, PrivilegedIntentsRequired, ValueError, ControlFailure):
            log.exception("Fatal configuration/authentication/control error; recovery disabled")
            raise
        except Exception:
            log.exception("Runtime failure, attempt %s/%s", attempt + 1, len(delays) + 1)
            if stopped():
                return
            if attempt == len(delays):
                raise
            for _ in range(delays[attempt]):
                heartbeat(attempt + 1, time.time())
                if stopped():
                    return
                await sleep(1)
