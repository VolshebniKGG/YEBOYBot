"""Offline integration smoke using a disposable copy, never real server data/login."""
import asyncio
import configparser
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from tools.legacy_launcher import configure_console, validate_environment
    configure_console()
    validate_environment(ROOT, require_packages=True)
    with tempfile.TemporaryDirectory(prefix="YEBOYBot legacy ") as directory:
        root = Path(directory)
        shutil.copytree(ROOT / "yeboybot", root / "yeboybot", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # Only source is copied; configuration, assets and user data stay untouched.
        sys.path.insert(0, str(root))
        os.chdir(root)
        from yeboybot.bot import create_bot, load_extensions
        from yeboybot.logging_setup import setup_logging, close_logging
        config = configparser.ConfigParser(interpolation=None)
        config.read_dict({"Credentials": {"Token": "offline-smoke-placeholder"},
                          "Chat": {"CommandPrefix": "!"}})
        setup_logging(root / "logs")

        async def check():
            bot = create_bot(config)
            async with bot:
                await load_extensions(bot, enable_music=False)
                assert set(bot.cogs) == {"Moderation", "User", "Help", "RankCog"}
                assert {"kick", "warn", "rank", "helpme", "info"} <= {cmd.name for cmd in bot.commands}
                assert "yeboybot.music" not in sys.modules
                assert not bot.is_ready()
        try:
            asyncio.run(check())
        finally:
            close_logging()
            os.chdir(ROOT)
    print("Offline legacy cogs: перевірено; Discord login не виконувався.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
