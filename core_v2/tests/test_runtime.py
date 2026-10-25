import logging
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core_v2.backup import backup
from core_v2.bot import Bot
from core_v2.commands import Core
from core_v2.config import Settings
from core_v2.runtime import InstanceLock, RedactingFormatter
from core_v2.security import UserError, target_check


def test_role_hierarchy():
    actor = SimpleNamespace(id=1, top_role=10)
    bot = SimpleNamespace(id=2, top_role=20)
    target = SimpleNamespace(id=3, top_role=10)
    with pytest.raises(UserError):
        target_check(actor, target, bot, 99)
    target_check(actor, target, bot, 1)
    target.top_role = 20
    with pytest.raises(UserError):
        target_check(actor, target, bot, 1)
    with pytest.raises(UserError):
        target_check(actor, actor, bot, 99)


def test_single_instance_lock(tmp_path):
    path = tmp_path / "bot.lock"
    with InstanceLock(path):
        with pytest.raises(RuntimeError):
            with InstanceLock(path):
                pass
    with InstanceLock(path):
        pass


def test_logging_redacts_traceback():
    formatter = RedactingFormatter(["secret-token"])
    try:
        raise RuntimeError("secret-token")
    except RuntimeError as error:
        record = logging.LogRecord("test", logging.ERROR, "", 1, "secret-token", (),
                                   (type(error), error, error.__traceback__))
    result = formatter.format(record)
    assert "secret-token" not in result
    assert "[REDACTED]" in result


def test_backup_and_refuse_overwrite(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "backup.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE test(value)")
        conn.execute("INSERT INTO test VALUES (42)")
    backup(source, target)
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT value FROM test").fetchone()[0] == 42
    with pytest.raises(FileExistsError):
        backup(source, target)
    with pytest.raises(FileExistsError):
        backup(source, source)


async def test_commands_register_without_network(tmp_path):
    bot = Bot(Settings("secret", f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}"))
    try:
        await bot.add_cog(Core(bot))
        names = {c.name for c in bot.tree.get_commands()}
        assert {"status", "rank", "warn", "timeout", "remind", "levels"} <= names
        assert len(names) == 12
        for command in bot.tree.get_commands():
            command.to_dict(bot.tree)
        assert not bot.intents.message_content
        assert not bot.intents.presences
        assert isinstance(bot, discord.AutoShardedClient)
    finally:
        await bot.db.close()


async def test_setup_sync_only_when_requested(tmp_path):
    bot = Bot(Settings("secret", f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}"))
    bot.tree.sync = AsyncMock()
    bot.deliver.start = lambda: None
    try:
        await bot.setup_hook()
        bot.tree.sync.assert_not_awaited()
    finally:
        await bot.db.close()


async def test_private_reminder_not_delivered_after_access_lost(tmp_path):
    bot = Bot(Settings("secret", f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}"))
    channel = SimpleNamespace(permissions_for=lambda member: SimpleNamespace(view_channel=False),
                              send=AsyncMock())
    guild = SimpleNamespace(get_channel_or_thread=lambda channel_id: channel,
                            fetch_member=AsyncMock(return_value=object()))
    bot.get_guild = lambda guild_id: guild
    bot.db.finish_job = AsyncMock()
    try:
        await bot.send_job(dict(guild_id=1, channel_id=2, user_id=3, id="job",
                                attempts=1, body="private"))
        channel.send.assert_not_awaited()
        bot.db.finish_job.assert_awaited_once_with(1, "job", 1, False, True)
    finally:
        await bot.db.close()


async def test_private_thread_requires_current_membership(tmp_path):
    from unittest.mock import MagicMock
    bot = Bot(Settings("secret", f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}"))
    channel = MagicMock(spec=discord.Thread)
    channel.is_private.return_value = True
    channel.send = AsyncMock()
    channel.fetch_member = AsyncMock(side_effect=discord.NotFound(
        SimpleNamespace(status=404, reason="Not Found"), "Unknown Member"))
    guild = SimpleNamespace(get_channel_or_thread=lambda channel_id: channel,
        fetch_member=AsyncMock(return_value=SimpleNamespace(id=3)))
    bot.get_guild = lambda guild_id: guild
    bot.db.finish_job = AsyncMock()
    try:
        await bot.send_job(dict(guild_id=1, channel_id=2, user_id=3, id="job",
                                attempts=1, body="private thread"))
        channel.fetch_member.assert_awaited_once_with(3)
        channel.send.assert_not_awaited()
        bot.db.finish_job.assert_awaited_once_with(1, "job", 1, False, True)
    finally:
        await bot.db.close()


def test_windowless_runtime_keeps_redacted_file_logging(tmp_path, monkeypatch):
    from core_v2 import runtime
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runtime.sys, "stderr", None)
    captured = {}
    monkeypatch.setattr(runtime.logging, "basicConfig", lambda **kwargs: captured.update(kwargs))
    runtime.configure_logging(SimpleNamespace(token="synthetic-secret", database="synthetic-db-url"))
    handlers = captured["handlers"]
    try:
        assert len(handlers) == 1
        assert isinstance(handlers[0], runtime.RotatingFileHandler)
        record = logging.LogRecord("test", logging.ERROR, "", 1, "synthetic-secret synthetic-db-url", (), None)
        handlers[0].handle(record)
        handlers[0].flush()
        saved = (tmp_path / "data/v2/logs/bot.log").read_text(encoding="utf-8")
        assert "synthetic-secret" not in saved
        assert "synthetic-db-url" not in saved
        assert "[REDACTED]" in saved
    finally:
        for handler in handlers:
            handler.close()
