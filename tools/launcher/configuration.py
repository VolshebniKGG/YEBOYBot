"""Secret-safe interactive configuration, preserving unrelated dotenv settings."""
import getpass
import os
from pathlib import Path
import re
import tempfile
import warnings

from dotenv import dotenv_values, set_key
from core_v2.runtime import InstanceLock

ROOT = Path(__file__).resolve().parents[2]


class ConfigurationError(ValueError):
    pass


def save(root, token, guild, sync):
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+", token):
        raise ConfigurationError("Токен порожній або містить неприпустимі символи.")
    if sync not in {"test", "none"}:
        raise ConfigurationError("Майстер підтримує test або none.")
    if guild and (not guild.isascii() or not guild.isdecimal() or not 0 < int(guild) < 2**64):
        raise ConfigurationError("ID сервера має бути додатним числом Discord.")
    if sync == "test" and not guild:
        raise ConfigurationError("Для тестових команд потрібен ID сервера.")
    env = root / ".env.v2"
    with InstanceLock(root / "data/v2/config.lock"):
        original = env.read_text(encoding="utf-8") if env.exists() else (root / ".env.v2.example").read_text(encoding="utf-8")
        descriptor, name = tempfile.mkstemp(prefix=".env.v2.", suffix=".tmp", dir=root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(original)
            for key, value in {"DISCORD_TOKEN": token, "TEST_GUILD_ID": guild, "SYNC_COMMANDS": sync}.items():
                set_key(str(temporary), key, value)
            os.replace(temporary, env)
        finally:
            temporary.unlink(missing_ok=True)


def wizard(root=ROOT):
    print("Discord Developer Portal: https://discord.com/developers/applications")
    print("Виберіть application → Bot → Token. Нікому не передавайте токен.")
    print("ID сервера: увімкніть Developer Mode у Discord та скопіюйте ID сервера.")
    env = root / ".env.v2"
    values = dotenv_values(env) if env.exists() else {}
    try:
        # Refuse getpass's echoing fallback on redirected stdin or unsupported consoles.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("Bot Token (Enter — зберегти наявний): ").strip()
    except getpass.GetPassWarning:
        raise ConfigurationError("Безпечне приховане введення недоступне. Відкрийте YEBOYBot.cmd у CMD.") from None
    token = token or values.get("DISCORD_TOKEN") or ""
    guild = input("ID тестового сервера (Enter — зберегти наявний): ").strip() or values.get("TEST_GUILD_ID") or ""
    sync = "test" if guild else "none"
    save(root, token, guild, sync)
    print("Конфігурацію збережено. Інші параметри залишено. Токен не перевірявся через Discord API.")
    if os.getenv("DISCORD_TOKEN") or os.getenv("TEST_GUILD_ID") or os.getenv("SYNC_COMMANDS"):
        print("Увага: змінні середовища мають пріоритет над .env.v2.")
    print("Перезапустіть працюючий бот через [2], щоб застосувати зміни.")
