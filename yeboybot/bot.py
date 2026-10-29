"""Legacy prefix-command bot; importing this module does not start or configure it."""

from __future__ import annotations

import asyncio
import configparser
import importlib
import logging
from pathlib import Path

import discord
from discord.ext import commands

from .logging_setup import refresh_logging, setup_logging


ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger("bot")
bot: commands.Bot | None = None


def load_config(config_path: str | Path | None = None) -> configparser.ConfigParser:
    """Read legacy configuration only when explicitly starting/creating a bot."""
    path = Path(config_path) if config_path is not None else ROOT / "config" / "options.ini"
    config = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8-sig") as source:
            config.read_file(source)
    except FileNotFoundError:
        raise ValueError(f"Файл конфігурації {path} не знайдено.") from None
    except (configparser.Error, UnicodeError):
        # Parser errors may contain whole credential lines. Never echo them.
        raise ValueError("Некоректний файл legacy-конфігурації options.ini.") from None
    if not config.get("Credentials", "Token", fallback="").strip():
        raise ValueError("Токен не вказано у файлі legacy-конфігурації.")
    return config


def create_bot(config: configparser.ConfigParser | None = None) -> commands.Bot:
    """Create the historical bot; callers may supply already-validated settings."""
    if config is None:
        config = load_config()
    instance = commands.Bot(
        command_prefix=config.get("Chat", "CommandPrefix", fallback="!"),
        intents=discord.Intents.all(),
    )

    @instance.event
    async def on_ready():
        logger.info("Бот запущено як %s (%s)", instance.user, instance.user.id)

    return instance


async def load_extensions(bot_instance: commands.Bot, enable_music: bool = False) -> None:
    """Load existing cogs; music dependencies and prerequisites are opt-in."""
    names = ["moderation", "user", "help", "rank"]
    if enable_music:
        names.append("music")
    try:
        modules = [importlib.import_module(f".{name}", __package__) for name in names]
    finally:
        # Also secure the error path if an optional dependency failed to import.
        # Old cogs add unfiltered handlers at import; detach them before setup.
        refresh_logging()
    for module in modules:
        await module.setup(bot_instance)
    logger.info("Усі вибрані legacy-розширення (Cog-и) завантажено успішно.")


async def main(enable_music: bool = False, *, config_path: str | Path | None = None) -> None:
    """Run the legacy client with cleanup on extension, login or network failure."""
    global bot
    config = load_config(config_path)
    token = config.get("Credentials", "Token").strip()
    credentials = [value for _, value in config.items("Credentials", raw=True)]
    setup_logging(ROOT / "logs" / "legacy", secrets=[token, *credentials])
    logging.getLogger("discord").setLevel(logging.WARNING)
    bot = create_bot(config)
    try:
        async with bot:
            await load_extensions(bot, enable_music=enable_music)
            await bot.start(token)
    except Exception:
        logger.exception("Помилка при запуску legacy-бота.")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        # Detailed exceptions have already gone through the redacting logger.
        # Startup/configuration failures may occur before that logger exists.
        raise SystemExit("Legacy-бот не запущено. Перевірте конфігурацію та logs/legacy.") from None
