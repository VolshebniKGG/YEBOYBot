"""Configuration/install boundaries; never download an installer in tests."""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from dotenv import dotenv_values

from core_v2 import config
from core_v2.bot import Bot
from core_v2.config import Settings, options_token
from core_v2 import media_environment as environment
from tools.launcher import media


def setup_root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/example_options.ini").write_text("[Credentials]\nToken=bot_token\n[MusicBot]\nDefaultVolume=.25\n", encoding="utf-8")
    (tmp_path / ".env.v2.example").write_text("DISCORD_TOKEN=\nENABLE_MUSIC=false\nFFMPEG_PATH=ffmpeg\n", encoding="utf-8")
    return tmp_path


def test_options_copy_is_exclusive_and_preserves_private_existing_configuration(tmp_path):
    root = setup_root(tmp_path)
    path = media.prepare_options(root)
    assert path.read_bytes() == (root / "config/example_options.ini").read_bytes()
    path.write_text("[Credentials]\nToken=synthetic-private-value\n", encoding="utf-8")
    assert media.prepare_options(root).read_text() == "[Credentials]\nToken=synthetic-private-value\n"


def test_music_flag_is_atomic_preserving_token_database_and_other_options(tmp_path):
    root = setup_root(tmp_path)
    path = root / ".env.v2"
    path.write_text("DISCORD_TOKEN=synthetic-private\nDATABASE_URL=sqlite+aiosqlite:///custom.db\nOTHER=yes\n", encoding="utf-8")
    media.save_enabled(root, True)
    values = dotenv_values(path)
    assert values["DISCORD_TOKEN"] == "synthetic-private"
    assert values["DATABASE_URL"] == "sqlite+aiosqlite:///custom.db"
    assert values["OTHER"] == "yes" and values["ENABLE_MUSIC"] == "true"
    media.save_enabled(root, False)
    assert dotenv_values(path)["ENABLE_MUSIC"] == "false"
    assert not list(root.glob(".env.v2.*.tmp"))


def test_options_token_uses_ini_without_interpolation_or_repr_exposure(tmp_path):
    root = setup_root(tmp_path)
    assert options_token(root) == ""
    media.prepare_options(root)
    assert options_token(root) == ""
    (root / "config/options.ini").write_text("\ufeff[Credentials]\nToken=synthetic%token\n", encoding="utf-8")
    token = options_token(root)
    assert token == "synthetic%token"
    assert token not in repr(Settings(token, "sqlite+aiosqlite:///data.db"))


@pytest.mark.parametrize("body", ["invalid synthetic-secret", "[Credentials]\nToken=a\nToken=synthetic-secret", "[Credentials]\nToken=" + "x" * 1048576],
                         ids=["malformed", "duplicate-token", "over-1MiB"])
def test_invalid_ini_never_echoes_payload(tmp_path, body):
    root = setup_root(tmp_path)
    (root / "config/options.ini").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        options_token(root)
    assert "synthetic-secret" not in str(caught.value)


def test_environment_token_wins_without_reading_broken_fallback(tmp_path, monkeypatch):
    root = setup_root(tmp_path)
    monkeypatch.setattr(config, "ROOT", root)
    monkeypatch.setattr(config, "load_dotenv", lambda path: None)
    for key in ("TEST_GUILD_ID", "SHARD_COUNT", "SYNC_COMMANDS", "DATABASE_URL", "ENABLE_MEMBERS", "ENABLE_MUSIC"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "synthetic-env")
    (root / "config/options.ini").write_text("invalid secret", encoding="utf-8")
    assert Settings.load().token == "synthetic-env"
    monkeypatch.setenv("DISCORD_TOKEN", "")
    (root / "config/options.ini").write_text("[Credentials]\nToken=synthetic-ini\n", encoding="utf-8")
    assert Settings.load().token == "synthetic-ini"


def test_ffmpeg_resolves_bundled_or_configured_path_independently_of_cwd(tmp_path, monkeypatch):
    root = setup_root(tmp_path)
    monkeypatch.setattr(environment.shutil, "which", lambda value: None)
    executable = root / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    executable.parent.mkdir()
    executable.touch()
    assert environment.ffmpeg_path(root=root) == str(executable.resolve())
    custom = root / "Моя музика" / executable.name
    custom.parent.mkdir()
    custom.touch()
    monkeypatch.chdir(tmp_path.parent)
    assert environment.ffmpeg_path(str(custom.relative_to(root)), root) == str(custom.resolve())


def test_ffmpeg_missing_is_controlled_and_not_downloaded(tmp_path, monkeypatch):
    monkeypatch.setattr(environment.shutil, "which", lambda value: None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(ValueError, match="FFmpeg"):
        environment.ffmpeg_path(root=tmp_path)


def test_ffmpeg_install_on_linux_gives_instructions_without_running_installer(tmp_path, monkeypatch, capsys):
    if os.name == "nt":
        pytest.skip("Linux fallback; native Windows installer boundary covered separately")
    run = MagicMock()
    monkeypatch.setattr(media.subprocess, "run", run)
    assert media.install_ffmpeg(tmp_path) is False
    run.assert_not_called()
    assert "Linux" in capsys.readouterr().out


def test_ffmpeg_windows_install_requires_explicit_consent_and_keeps_hash_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(media.shutil, "which", lambda name: "C:/trusted/winget.exe")
    run = MagicMock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(media.subprocess, "run", run)
    monkeypatch.setattr(media, "ffmpeg_path", lambda **kwargs: "C:/installed/ffmpeg.exe")
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert not media.install_ffmpeg(tmp_path)
    run.assert_not_called()
    monkeypatch.setattr("builtins.input", lambda prompt: "ВСТАНОВИТИ FFMPEG")
    assert media.install_ffmpeg(tmp_path)
    arguments = run.call_args.args[0]
    assert arguments == ["C:/trusted/winget.exe", "install", "--id", "Gyan.FFmpeg", "--exact",
        "--source", "winget", "--scope", "user", "--architecture", "x64", "--silent",
        "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"]
    assert "--ignore-security-hash" not in arguments and "--force" not in arguments
    assert not run.call_args.kwargs.get("shell")


@pytest.mark.asyncio
async def test_bot_uses_voice_only_member_cache_and_non_privileged_intents(tmp_path):
    bot = Bot(Settings("synthetic", f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}"))
    try:
        assert bot.intents.voice_states
        assert not bot.intents.members and not bot.intents.message_content and not bot.intents.presences
        assert bot._connection.member_cache_flags.voice and not bot._connection.member_cache_flags.joined
        bot.tree.sync = AsyncMock()
        bot.deliver.start = lambda: None
        await bot.setup_hook()
        assert bot.get_cog("VoiceXP") is not None
        assert bot.get_cog("Music") is None
        assert not bot.get_cog("VoiceXP").collect.is_running()
    finally:
        await bot.remove_cog("VoiceXP")
        await bot.db.close()


def test_fixed_pynacl_supports_discord_aead_transport_without_network():
    import discord
    import nacl.secret
    import struct
    client = object.__new__(discord.VoiceClient)
    client._incr_nonce = 0
    client._connection = SimpleNamespace(secret_key=bytes(range(32)))
    header = b"\x80\x78" + bytes(10)
    data = b"synthetic-opus-packet"
    encrypt = client._encrypt_aead_xchacha20_poly1305_rtpsize
    packets = [encrypt(header, data) for _ in range(3)]
    assert len(set(packets)) == 3 and client._incr_nonce == 3
    box = nacl.secret.Aead(client.secret_key)
    for index, packet in enumerate(packets):
        assert packet[:12] == header
        assert packet[-4:] == struct.pack(">I", index)
        nonce = packet[-4:] + bytes(20)
        assert box.decrypt(packet[12:-4], header, nonce) == data


def test_js_runtime_discovery_requires_no_download_and_supports_custom_root(tmp_path, monkeypatch):
    monkeypatch.setattr(environment.shutil, "which", lambda value: None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(ValueError, match="Deno"):
        environment.javascript_runtime(tmp_path)
    executable = tmp_path / "bin" / ("deno.exe" if os.name == "nt" else "deno")
    executable.parent.mkdir()
    executable.touch()
    assert environment.javascript_runtime(tmp_path) == {"deno": {"path": str(executable.resolve())}}


def test_deno_windows_install_is_explicit_exact_and_per_user(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(media.shutil, "which", lambda value: "C:/trusted/winget.exe")
    monkeypatch.setattr(environment, "javascript_runtime", lambda root: {"deno": {"path": "installed"}})
    run = MagicMock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(media.subprocess, "run", run)
    monkeypatch.setattr("builtins.input", lambda prompt: "ВСТАНОВИТИ DENO")
    assert media.install_deno(tmp_path)
    arguments = run.call_args.args[0]
    assert arguments[arguments.index("--id") + 1] == "DenoLand.Deno"
    assert arguments[arguments.index("--scope") + 1] == "user"
    assert "--exact" in arguments and "--force" not in arguments


@pytest.mark.asyncio
async def test_music_enabled_setup_registers_group_without_connecting_or_starting_tasks(tmp_path, monkeypatch):
    import core_v2.music
    monkeypatch.setattr(core_v2.music, "ffmpeg_path", lambda configured, root: "synthetic-ffmpeg")
    bot = Bot(Settings("synthetic", f"sqlite+aiosqlite:///{tmp_path / 'enabled.db'}", music=True))
    bot.tree.sync = AsyncMock()
    bot.deliver.start = lambda: None
    try:
        await bot.setup_hook()
        cog = bot.get_cog("Music")
        assert cog is not None and not cog.autojoin.is_running()
        group = bot.tree.get_command("music")
        assert group is not None and len(group.commands) == 8
        assert {command.name for command in group.commands} == {
            "play", "join", "queue", "skip", "stop", "autoplay", "pause", "resume"}
        assert len(bot.tree.get_commands()) == 23
        assert bot.get_cog("Community") is not None
        assert bot.get_cog("AutoMod") is not None
        assert {"help", "modules"} <= {command.name for command in bot.tree.get_commands()}
        for command in bot.tree.get_commands():
            command.to_dict(bot.tree)
        assert not bot.voice_clients and not cog.manager.players
    finally:
        await bot.remove_cog("Music")
        await bot.remove_cog("VoiceXP")
        await bot.db.close()
