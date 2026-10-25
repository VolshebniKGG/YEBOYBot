"""Legacy compatibility/lifecycle tests without legacy extras or Discord login."""

import configparser
import io
import logging
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from discord.ext import commands

from yeboybot import bot as legacy
from yeboybot import logging_setup


@pytest.fixture(autouse=True)
def restore_logging():
    """The real entrypoint owns a process; these tests share pytest's process."""
    root = logging.getLogger()
    loggers = [root, *(item for item in logging.root.manager.loggerDict.values()
                       if isinstance(item, logging.Logger))]
    previous = {item: (item.handlers[:], item.level, item.propagate) for item in loggers}
    yield
    logging_setup.close_logging()
    for item, (handlers, level, propagate) in previous.items():
        item.handlers[:] = handlers
        item.setLevel(level)
        item.propagate = propagate


def write_config(root, *, token="synthetic-legacy-token"):
    path = root / "config" / "options.ini"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"[Credentials]\nToken = {token}\nSpotify_ClientSecret = synthetic-spotify-secret\n"
        "Other_Key = synthetic-other-credential\n[Chat]\nCommandPrefix = ?\n",
        encoding="utf-8",
    )
    return path


def test_import_has_no_configuration_or_log_side_effects(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "bot.log").write_text("historical log\n", encoding="utf-8")
    (logs / "errors.log").write_text("historical error\n", encoding="utf-8")
    script = '''
import configparser, importlib, logging, sys
def forbidden(*args, **kwargs):
    raise AssertionError("configuration read during import")
configparser.ConfigParser.read = forbidden
configparser.ConfigParser.read_file = forbidden
root = logging.getLogger()
handlers = root.handlers[:]
module = importlib.import_module("yeboybot.bot")
assert module.bot is None
assert root.handlers == handlers
for name in ("moderation", "user", "help", "rank", "music", "autoplaylist"):
    assert "yeboybot." + name not in sys.modules
'''
    # A fresh process prevents another test's cached modules from hiding effects.
    script = f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n" + script
    result = subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert (logs / "bot.log").read_text() == "historical log\n"
    assert (logs / "errors.log").read_text() == "historical error\n"
    assert not (logs / "legacy").exists()


def test_configuration_accepts_literal_percent_and_utf8_bom(tmp_path):
    path = write_config(tmp_path, token="synthetic%literal%token")
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    config = legacy.load_config(path)
    assert config.get("Credentials", "Token") == "synthetic%literal%token"
    assert config.get("Chat", "CommandPrefix") == "?"


@pytest.mark.parametrize("contents", [
    "[Credentials]\nToken = \n",
    "[Other]\nToken = synthetic-credential\n",
    "[Credentials]\nToken=synthetic-credential\nToken=synthetic-credential\n",
    "synthetic-credential\n",
])
def test_bad_configuration_errors_do_not_echo_credentials(tmp_path, contents):
    path = tmp_path / "options.ini"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError) as error:
        legacy.load_config(path)
    assert "synthetic-credential" not in str(error.value)


def test_missing_configuration_has_clear_error(tmp_path):
    with pytest.raises(ValueError, match="не знайдено"):
        legacy.load_config(tmp_path / "missing.ini")


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_music", [False, True])
async def test_cogs_use_async_discord_py_api_and_music_is_opt_in(tmp_path, monkeypatch, enable_music):
    requested = []
    loaded = []

    def import_cog(name, package):
        requested.append(name)
        cog_class = type(f"Fixture_{name[1:]}", (commands.Cog,), {})

        async def setup(instance):
            await instance.add_cog(cog_class())
            loaded.append(name)

        return SimpleNamespace(setup=setup)

    config = configparser.ConfigParser()
    config.read_dict({"Chat": {"CommandPrefix": "?"}})
    monkeypatch.setattr(legacy.importlib, "import_module", import_cog)
    instance = legacy.create_bot(config)
    async with instance:
        await legacy.load_extensions(instance, enable_music=enable_music)
        expected = [".moderation", ".user", ".help", ".rank"]
        if enable_music:
            expected.append(".music")
        assert requested == expected
        assert loaded == expected
        assert set(instance.cogs) == {f"Fixture_{name[1:]}" for name in expected}
        assert instance.command_prefix == "?"
    assert instance.is_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["import", "setup", "login"])
async def test_main_closes_client_and_redacts_failures(tmp_path, monkeypatch, capsys, failure):
    write_config(tmp_path)
    monkeypatch.setattr(legacy, "ROOT", tmp_path)
    config = legacy.load_config(tmp_path / "config" / "options.ini")
    instance = legacy.create_bot(config)
    start = AsyncMock(side_effect=RuntimeError("synthetic-legacy-token"))
    monkeypatch.setattr(instance, "start", start)
    monkeypatch.setattr(legacy, "create_bot", lambda config: instance)
    unsafe_output = io.StringIO()

    def import_cog(name, package):
        if name == ".moderation":
            # Reproduce old moderation's direct, unfiltered console handler.
            logging.getLogger("bot").addHandler(logging.StreamHandler(unsafe_output))
        if failure == "import" and name == ".user":
            raise ImportError("synthetic-spotify-secret")

        async def setup(client):
            if failure == "setup":
                raise RuntimeError("synthetic-other-credential")

        return SimpleNamespace(setup=setup)

    monkeypatch.setattr(legacy.importlib, "import_module", import_cog)
    with pytest.raises((RuntimeError, ImportError)):
        await legacy.main()
    assert instance.is_closed()
    if failure == "login":
        start.assert_awaited_once_with("synthetic-legacy-token")
    else:
        start.assert_not_awaited()
    assert unsafe_output.getvalue() == ""
    outputs = capsys.readouterr()
    rendered = outputs.out + outputs.err
    rendered += (tmp_path / "logs" / "legacy" / "bot.log").read_text(encoding="utf-8")
    rendered += (tmp_path / "logs" / "legacy" / "errors.log").read_text(encoding="utf-8")
    assert "Traceback" in rendered
    assert "[ПРИХОВАНО]" in rendered
    for credential in ("synthetic-legacy-token", "synthetic-spotify-secret", "synthetic-other-credential"):
        assert credential not in rendered


def test_logs_append_and_all_handlers_redact_messages_and_tracebacks(tmp_path, capsys):
    directory = tmp_path / "logs" / "legacy"
    directory.mkdir(parents=True)
    (directory / "bot.log").write_text("previous bot run\n", encoding="utf-8")
    (directory / "errors.log").write_text("previous error\n", encoding="utf-8")
    old_output = io.StringIO()
    root = logging.getLogger()
    root.addHandler(logging.StreamHandler(old_output))
    third_party = logging.getLogger("legacy_fixture_dependency")
    third_party.addHandler(logging.StreamHandler(old_output))
    third_party.propagate = False
    logger = logging_setup.setup_logging(directory, secrets=["synthetic-token", "synthetic-password"])
    handlers = root.handlers[:]
    assert logging_setup.setup_logging(directory, secrets=["synthetic-additional"]) is logger
    assert root.handlers == handlers
    assert len(handlers) == 3
    logger.info("token %s password %s", "synthetic-token", "synthetic-password")
    try:
        raise RuntimeError("synthetic-password synthetic-additional")
    except RuntimeError:
        third_party.exception("request credential %s", "synthetic-token")
    assert old_output.getvalue() == ""
    console = capsys.readouterr().err
    bot_log = (directory / "bot.log").read_text(encoding="utf-8")
    error_log = (directory / "errors.log").read_text(encoding="utf-8")
    assert bot_log.startswith("previous bot run\n")
    assert error_log.startswith("previous error\n")
    assert "Traceback" in console + error_log
    for secret in ("synthetic-token", "synthetic-password", "synthetic-additional"):
        assert secret not in console + bot_log + error_log
    assert "[ПРИХОВАНО]" in console + bot_log + error_log
    logging_setup.close_logging()
    assert not set(handlers) & set(root.handlers)
    assert all(handler.stream is None for handler in handlers if isinstance(handler, logging.FileHandler))
