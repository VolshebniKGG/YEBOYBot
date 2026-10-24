import asyncio
import logging
import os

from .config import ROOT, Settings
from .runtime import InstanceLock, configure_logging


async def run(settings):
    from .bot import Bot
    async with Bot(settings) as bot:
        await bot.start(settings.token)


def main():
    os.chdir(ROOT)
    try:
        settings = Settings.load()
    except (ValueError, OSError):
        print("Помилка .env.v2: перевірте DISCORD_TOKEN, TEST_GUILD_ID, SYNC_COMMANDS та DATABASE_URL.")
        raise SystemExit(2) from None
    configure_logging(settings)
    try:
        if settings.music:
            raise RuntimeError("Музичний модуль ще не підключено до цієї версії Core v2.")
        with InstanceLock("data/v2/bot.lock"):
            asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.getLogger(__name__).exception("Bot stopped")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
