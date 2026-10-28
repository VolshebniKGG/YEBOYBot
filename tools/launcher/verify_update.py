"""Post-update check, deliberately without a Discord connection."""
import asyncio
import os

from core_v2.config import ROOT, Settings
from core_v2.db import Database


async def verify():
    settings = Settings.load()
    db = Database(settings.database)
    try:
        await db.initialize()
        if not await db.health():
            raise RuntimeError("Database health failed")
        from core_v2.commands import Core
        from core_v2.guide import Guide
        from core_v2.bot import Bot
        bot = Bot(settings)
        try:
            await bot.add_cog(Core(bot))
            await bot.add_cog(Guide(bot))
            for command in bot.tree.get_commands():
                command.to_dict(bot.tree)
        finally:
            await bot.remove_cog("Guide")
            await bot.db.close()
    finally:
        await db.close()


if __name__ == "__main__":
    os.chdir(ROOT)
    try:
        asyncio.run(verify())
    except Exception:
        # No raw exception/URL/token in subprocess output.
        print("Перевірка конфігурації, схеми або команд не пройдена.")
        raise SystemExit(1) from None
    print("Конфігурацію, БД та команди перевірено без підключення до Discord.")
