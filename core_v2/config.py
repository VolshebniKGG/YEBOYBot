from dataclasses import dataclass, field
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def flag(name):
    value = os.getenv(name, "false").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name}: use true or false")
    return value == "true"


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    database: str = field(repr=False)
    test_guild: int | None = None
    sync: str = "none"
    members: bool = False
    music: bool = False
    ffmpeg: str = "ffmpeg"
    shard_count: int | None = None

    @classmethod
    def load(cls, require_token=True):
        load_dotenv(ROOT / ".env.v2")
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if require_token and not token:
            raise ValueError("Заповніть DISCORD_TOKEN у .env.v2")
        sync = os.getenv("SYNC_COMMANDS", "none").strip()
        guild = int(os.getenv("TEST_GUILD_ID") or 0) or None
        shards = int(os.getenv("SHARD_COUNT") or 0) or None
        if sync not in {"none", "test", "global"} or (sync == "test" and not guild):
            raise ValueError("SYNC_COMMANDS: none/test/global; test потребує TEST_GUILD_ID")
        if (guild is not None and guild <= 0) or (shards is not None and shards <= 0):
            raise ValueError("Guild ID and shard count must be positive")
        database = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/v2/yeboy.db")
        if not database.startswith(("sqlite+aiosqlite:///", "postgresql+asyncpg://")):
            raise ValueError("DATABASE_URL must use sqlite+aiosqlite or postgresql+asyncpg")
        return cls(token, database, guild, sync, flag("ENABLE_MEMBERS"), flag("ENABLE_MUSIC"),
                   os.getenv("FFMPEG_PATH", "ffmpeg"), shards)
