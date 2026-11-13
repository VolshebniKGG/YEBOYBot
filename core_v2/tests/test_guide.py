"""Private guide commands and real UI components, without a Discord connection."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import app_commands
import pytest

from core_v2.bot import Bot
from core_v2.commands import Core
from core_v2.config import Settings
from core_v2 import guide as module
from core_v2.guide import Guide
from core_v2.security import UserError


def interaction(guild, member):
    done = [False]

    async def defer(**kwargs):
        done[0] = True

    return SimpleNamespace(guild=guild, guild_id=guild.id, user=member,
        response=SimpleNamespace(defer=AsyncMock(side_effect=defer), is_done=lambda: done[0],
                                  send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))),
        edit_original_response=AsyncMock())


@pytest.fixture
async def guide(tmp_path):
    bot = Bot(Settings("synthetic", f"sqlite+aiosqlite:///{tmp_path / 'guide.db'}"))
    await bot.add_cog(Core(bot))
    cog = Guide(bot)
    await bot.add_cog(cog)
    guild = MagicMock(spec=discord.Guild)
    guild.id = 10
    member = MagicMock(spec=discord.Member)
    member.id, member.guild = 20, guild
    member.guild_permissions = discord.Permissions.none()
    fixture = SimpleNamespace(bot=bot, cog=cog, guild=guild, member=member,
                              interaction=interaction(guild, member))
    yield fixture
    await bot.remove_cog("Guide")
    await bot.remove_cog("Core")
    await bot.db.close()


async def invoke(fixture, name, *args, **kwargs):
    await getattr(Guide, name).callback(fixture.cog, fixture.interaction, *args, **kwargs)


def reply(fixture):
    arguments = fixture.interaction.followup.send.await_args.kwargs
    assert arguments["ephemeral"] is True
    assert arguments["allowed_mentions"].to_dict()["parse"] == []
    return arguments


async def test_help_is_registered_private_with_live_catalog_and_interactive_view(guide):
    f = guide
    await invoke(f, "help")
    f.interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    output = reply(f)
    view = output["view"]
    assert view.user_id == 20 and view.guild_id == 10 and view.timeout == 120
    assert view in f.cog.views
    assert {entry.name for entry in view.entries} == {command.name for command in f.bot.tree.get_commands()}
    assert "help" in {entry.name for entry in view.entries}
    assert "modules" in {entry.name for entry in view.entries}
    assert len(f.bot.tree.get_commands()) == 20
    assert view.message is f.interaction.followup.send.return_value
    assert "private" not in str(output["embed"].to_dict())


@pytest.mark.parametrize("name", ["help", "modules"])
async def test_guide_commands_reject_dm_before_defer(guide, name):
    guide.interaction.guild = None
    with pytest.raises(UserError, match="сервері"):
        await invoke(guide, name)
    guide.interaction.response.defer.assert_not_awaited()
    guide.interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize("category,page", [("secret", 1), ("general", 0), ("general", 11), ("general", True)])
async def test_invalid_help_arguments_never_create_view(guide, category, page):
    with pytest.raises(UserError):
        await invoke(guide, "help", app_commands.Choice(name="topic", value=category), page)
    assert not guide.cog.views
    guide.interaction.followup.send.assert_not_awaited()


async def test_excess_page_has_safe_private_error_and_no_view(guide):
    await invoke(guide, "help", page=10)
    call = guide.interaction.followup.send.await_args
    assert call.kwargs["ephemeral"] is True and "сторінок" in call.args[0]
    assert not guide.cog.views


async def test_select_and_pagination_use_real_components_without_sync_or_writes(guide):
    f = guide
    f.bot.tree.sync = AsyncMock()
    f.bot.db.configure = AsyncMock()
    await invoke(f, "help")
    view = reply(f)["view"]
    select_click = interaction(f.guild, f.member)
    view.topic._values = ["moderation"]
    await view.topic.callback(select_click)
    assert view.category == "moderation" and view.page == 1
    assert view.previous.disabled and not view.next.disabled
    assert select_click.response.defer.await_count == 1
    assert select_click.edit_original_response.await_args.kwargs["allowed_mentions"].to_dict()["parse"] == []
    await view.next.callback(interaction(f.guild, f.member))
    assert view.page == 2 and view.next.disabled
    await view.previous.callback(interaction(f.guild, f.member))
    assert view.page == 1
    f.bot.tree.sync.assert_not_awaited()
    f.bot.db.configure.assert_not_awaited()


@pytest.mark.parametrize("wrong", ["author", "guild"])
async def test_components_are_bound_to_author_and_guild(guide, wrong):
    f = guide
    await invoke(f, "help")
    view = reply(f)["view"]
    click = interaction(f.guild, f.member)
    if wrong == "author":
        click.user = SimpleNamespace(id=21)
    else:
        click.guild_id = 11
    await view.next.callback(click)
    assert view.page == 1
    click.edit_original_response.assert_not_awaited()
    call = click.response.send_message.await_args
    assert call.kwargs["ephemeral"] is True
    assert call.kwargs["allowed_mentions"].to_dict()["parse"] == []


async def test_forged_select_value_is_rejected_without_metadata_echo(guide):
    f = guide
    await invoke(f, "help")
    view = reply(f)["view"]
    view.topic._values = ["@everyone synthetic-private"]
    click = interaction(f.guild, f.member)
    await view.topic.callback(click)
    assert view.category == "general"
    click.edit_original_response.assert_not_awaited()
    assert "synthetic-private" not in click.response.send_message.await_args.args[0]


async def test_view_refreshes_removed_commands_and_clamps_page(guide):
    f = guide
    await invoke(f, "help", app_commands.Choice(name="Модерація", value="moderation"), 2)
    view = reply(f)["view"]
    f.bot.tree.remove_command("ban")
    await view.next.callback(interaction(f.guild, f.member))
    assert all(entry.name != "ban" for entry in view.entries)
    assert view.page == 1 and view.next.disabled


async def test_view_limit_keeps_static_help_and_slash_pagination_available(guide, monkeypatch):
    f = guide
    monkeypatch.setattr(module, "MAX_HELP_VIEWS", 1)
    await invoke(f, "help")
    original = reply(f)["view"]
    f.interaction = interaction(f.guild, f.member)
    await invoke(f, "help", app_commands.Choice(name="Модерація", value="moderation"), 2)
    assert reply(f)["view"] is None
    assert f.cog.views == {original}
    assert len(reply(f)["embed"].fields) == 1


async def test_timeout_disables_controls_releases_slot_and_later_click_is_private(guide):
    f = guide
    await invoke(f, "help")
    view = reply(f)["view"]
    await view.on_timeout()
    assert view.expired and view.is_finished() and view not in f.cog.views
    assert all(child.disabled for child in view.children)
    view.message.edit.assert_awaited_once()
    click = interaction(f.guild, f.member)
    await view.next.callback(click)
    click.edit_original_response.assert_not_awaited()
    assert "минув" in click.response.send_message.await_args.args[0]


@pytest.mark.parametrize("cancelled", [False, True], ids=["lost-response", "cancelled-response"])
async def test_failed_help_send_does_not_leak_view(guide, cancelled):
    f = guide
    f.interaction.followup.send.side_effect = asyncio.CancelledError() if cancelled else OSError("private")
    with pytest.raises(asyncio.CancelledError if cancelled else OSError):
        await invoke(f, "help")
    assert not f.cog.views


async def test_unload_during_defer_does_not_register_late_view(guide):
    f = guide
    entered, release = asyncio.Event(), asyncio.Event()

    async def defer(**kwargs):
        entered.set()
        await release.wait()

    f.interaction.response.defer.side_effect = defer
    pending = asyncio.create_task(invoke(f, "help"))
    try:
        await entered.wait()
        await f.cog.cog_unload()
        release.set()
        with pytest.raises(UserError):
            await pending
        assert not f.cog.views
        f.interaction.followup.send.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_unload_during_send_disables_late_message_controls(guide):
    f = guide
    entered, release = asyncio.Event(), asyncio.Event()
    message = SimpleNamespace(edit=AsyncMock())

    async def send(**kwargs):
        entered.set()
        await release.wait()
        return message

    f.interaction.followup.send.side_effect = send
    pending = asyncio.create_task(invoke(f, "help"))
    try:
        await entered.wait()
        await f.cog.cog_unload()
        assert not f.cog.views
        release.set()
        await pending
        message.edit.assert_awaited_once()
        assert all(child.disabled for child in message.edit.await_args.kwargs["view"].children)
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_modules_forwards_current_guild_read_only_and_responds_privately(guide, monkeypatch):
    f = guide
    collect = AsyncMock(return_value=discord.Embed(title="Стан модулів"))
    monkeypatch.setattr(module, "collect_module_status", collect)
    await invoke(f, "modules")
    collect.assert_awaited_once_with(f.bot, f.guild)
    f.interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    assert reply(f)["embed"].title == "Стан модулів"
    assert not f.cog.views


async def test_help_panel_error_uses_only_safe_message_and_error_type(guide, caplog):
    f = guide
    await invoke(f, "help")
    view = reply(f)["view"]
    click = interaction(f.guild, f.member)
    await view.on_error(click, RuntimeError("synthetic-private credential"), view.topic)
    assert "RuntimeError" in caplog.text and "synthetic-private" not in caplog.text
    assert "synthetic-private" not in click.response.send_message.await_args.args[0]


async def test_expire_edit_failure_is_sanitized_and_still_releases_slot(guide, caplog):
    f = guide
    await invoke(f, "help")
    view = reply(f)["view"]
    view.message.edit.side_effect = discord.NotFound(SimpleNamespace(status=404, reason="missing"), "synthetic-private")
    await view.expire()
    assert view.expired and not f.cog.views
    assert "synthetic-private" not in caplog.text


async def test_guide_metadata_is_guild_only_and_has_bounded_page_parameter(guide):
    for name in ("help", "modules"):
        command = guide.bot.tree.get_command(name)
        assert command.guild_only and command.default_permissions is None
        command.to_dict(guide.bot.tree)
    parameters = {parameter.name: parameter for parameter in guide.bot.tree.get_command("help").parameters}
    assert parameters["page"].min_value == 1 and parameters["page"].max_value == 10


async def test_update_smoke_validates_real_guide_command_metadata_without_login(tmp_path, monkeypatch):
    from tools.launcher import verify_update
    configured = Settings("synthetic", f"sqlite+aiosqlite:///{tmp_path / 'update.db'}")
    monkeypatch.setattr(verify_update.Settings, "load", lambda: configured)
    original = app_commands.Command.to_dict
    serialized = []

    def record(command, tree):
        serialized.append(command.name)
        return original(command, tree)

    monkeypatch.setattr(app_commands.Command, "to_dict", record)
    await verify_update.verify()
    assert {"help", "modules", "rank", "warn"} <= set(serialized)
    assert len(serialized) == 20
