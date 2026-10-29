"""Real local CommandTree discovery without Discord fetch/sync or SQL calls."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord import app_commands
from discord.ext import commands
import pytest

from core_v2.help_catalog import (CATEGORY_LABELS, CommandEntry, MAX_ENTRIES, PAGE_SIZE,
                                  available_categories, collect_commands, page_count, render_help)


def tree():
    client = discord.Client(intents=discord.Intents.none())
    result = app_commands.CommandTree(client)
    result.fetch_commands = AsyncMock(side_effect=AssertionError("network prohibited"))
    result.sync = AsyncMock(side_effect=AssertionError("sync prohibited"))
    return result


async def callback(interaction: discord.Interaction):
    pass


def command(name, description="Опис команди"):
    return app_commands.Command(name=name, description=description, callback=callback)


def units(value):
    return len(value.encode("utf-16-le")) // 2


def assert_bounded(embed):
    total = units(embed.title or "") + units(embed.description or "") + units(embed.footer.text or "")
    assert len(embed.fields) <= PAGE_SIZE
    for field in embed.fields:
        assert units(field.name) <= 256
        assert units(field.value) <= 1024
        total += units(field.name) + units(field.value)
    assert total <= 6000


def entry(name="rank", category="rank", **kwargs):
    return CommandEntry(name=name, category=category, description=kwargs.get("description", "Опис"),
                        usage=kwargs.get("usage", "/" + name),
                        permissions=kwargs.get("permissions", "Перевіряються командою."))


def test_command_entry_is_immutable():
    value = entry()
    with pytest.raises(FrozenInstanceError):
        value.name = "changed"


def test_current_tree_without_remote_calls_is_the_only_source_of_commands():
    current = tree()
    current.add_command(command("rank"))
    current.add_command(command("help"))
    entries = collect_commands(current, 123)
    assert [item.name for item in entries] == ["help", "rank"]
    assert {item.category for item in entries} == {"general", "rank"}
    current.fetch_commands.assert_not_called()
    current.sync.assert_not_called()
    assert not any(item.name in {"setup", "tickets", "automod", "music play"} for item in entries)


def test_nested_groups_flatten_to_complete_leaf_names():
    current = tree()
    parent = app_commands.Group(name="tools", description="Tools")
    child = app_commands.Group(name="roles", description="Roles")
    child.add_command(command("inspect"))
    parent.add_command(child)
    current.add_command(parent)
    assert [item.name for item in collect_commands(current)] == ["tools roles inspect"]
    assert collect_commands(current)[0].usage == "/tools roles inspect"
    assert collect_commands(current)[0].category == "other"


def test_guild_root_replaces_all_global_descendants_without_merging_old_children():
    current = tree()
    global_group = app_commands.Group(name="music", description="Global")
    global_group.add_command(command("play"))
    global_group.add_command(command("queue"))
    current.add_command(global_group)
    local_group = app_commands.Group(name="music", description="Guild")
    local_group.add_command(command("stop", "Local stop"))
    current.add_command(local_group, guild=discord.Object(id=123))
    assert [item.name for item in collect_commands(current, 123)] == ["music stop"]
    assert [item.name for item in collect_commands(current, 124)] == ["music play", "music queue"]


def test_global_leaf_can_be_replaced_by_guild_group():
    current = tree()
    current.add_command(command("rank", "Global leaf"))
    local = app_commands.Group(name="rank", description="Local group")
    local.add_command(command("show"))
    current.add_command(local, guild=discord.Object(id=123))
    assert [item.name for item in collect_commands(current, 123)] == ["rank show"]


def test_context_menu_commands_are_not_advertised_as_slash_commands():
    current = tree()

    async def context_callback(interaction: discord.Interaction, user: discord.User):
        pass

    current.add_command(app_commands.ContextMenu(name="Inspect User", callback=context_callback))
    current.add_command(command("rank"))
    assert [item.name for item in collect_commands(current)] == ["rank"]


@pytest.mark.parametrize("guild_id", [False, True, 0, -1, 2**64, "123", 1.5])
def test_invalid_guild_scope_rejected_before_tree_access(guild_id):
    with pytest.raises(ValueError, match="guild"):
        collect_commands(object(), guild_id)


def test_usage_is_derived_from_actual_parameters_and_aliases():
    current = tree()

    @app_commands.rename(member="учасник")
    async def real(interaction: discord.Interaction, member: discord.Member, reason: str | None = None):
        pass

    current.add_command(app_commands.Command(name="inspect", description="Огляд", callback=real))
    assert collect_commands(current)[0].usage == "/inspect <учасник> [reason]"


def test_default_permission_metadata_is_used_for_new_commands():
    current = tree()
    custom = command("custom")
    custom.default_permissions = discord.Permissions(manage_roles=True)
    current.add_command(custom)
    assert "керувати ролями" in collect_commands(current)[0].permissions


def test_command_discovery_is_capped_without_discarding_unknown_categories():
    current = tree()
    for group_index in range(5):
        group = app_commands.Group(name=f"group{group_index}", description="Group")
        for index in range(25):
            group.add_command(command(f"leaf{index:02}"))
        current.add_command(group)
    entries = collect_commands(current)
    assert len(entries) == MAX_ENTRIES
    assert entries[0].name == "group0 leaf00" and entries[-1].name == "group3 leaf24"
    assert all(item.category == "other" for item in entries)


def test_discovery_is_stable_and_observes_later_removal_without_stale_cache():
    current = tree()
    for name in ("warnings", "levels", "help", "rank"):
        current.add_command(command(name))
    assert [item.name for item in collect_commands(current)] == sorted(["warnings", "levels", "help", "rank"])
    current.remove_command("warnings")
    assert not any(item.name == "warnings" for item in collect_commands(current))


def test_available_categories_are_populated_plus_general():
    assert available_categories(()) == ("general",)
    assert available_categories((entry(), entry("custom", "other"))) == ("general", "rank", "other")
    assert list(CATEGORY_LABELS) == ["general", "rank", "moderation", "reminders", "music", "community", "other"]


def test_general_page_lists_all_topic_counts_and_static_topic_navigation():
    embed = render_help((entry("help", "general"), entry(), entry("warn", "moderation")))
    assert "Ранг і XP: 1" in embed.description
    assert "Музика: 0" in embed.description and "/help topic" in embed.description
    assert "не підтверджує публікацію" in embed.description
    assert_bounded(embed)


def test_empty_music_explains_unavailable_module_without_active_music_usage():
    embed = render_help((entry(),), "music")
    assert "Музичні команди не підключені" in embed.fields[0].value
    assert "власника бота" in embed.fields[0].value
    assert "/music play" not in str(embed.to_dict())
    assert_bounded(embed)


@pytest.mark.parametrize("category", list(CATEGORY_LABELS))
def test_empty_categories_have_a_bounded_page(category):
    assert page_count((), category) == 1
    embed = render_help((), category)
    assert len(embed.fields) == 1 and "1/1" in embed.footer.text
    assert_bounded(embed)


def test_pagination_preserves_entries_without_repeating_and_clamps_stale_page():
    entries = tuple(entry(str(i), "moderation") for i in range(21))
    assert page_count(entries, "moderation") == 3
    names = []
    for page in range(1, 4):
        embed = render_help(entries, "moderation", page)
        names.extend(field.name for field in embed.fields)
        assert_bounded(embed)
    assert names == [f"/{i}" for i in range(21)]
    assert [field.name for field in render_help(entries, "moderation", 99).fields] == ["/20"]


@pytest.mark.parametrize("page", [False, True, 0, -1, 1.5, "1"])
def test_bad_page_does_not_echo_input(page):
    with pytest.raises(ValueError, match="page"):
        render_help((), page=page)


@pytest.mark.parametrize("category", [None, 123, [], "private-token"])
def test_unknown_categories_fail_without_echo(category):
    with pytest.raises(ValueError) as caught:
        render_help((), category)
    assert "private-token" not in str(caught.value)


def test_rendering_has_strict_utf16_aggregate_and_field_bounds_for_extreme_metadata():
    text = "😀@everyone **_`ім'я`_**\u202e\0\ud800" * 1000
    entries = tuple(CommandEntry(text, text, text, text, "general") for _ in range(10))
    embed = render_help(entries)
    assert_bounded(embed)
    encoded = str(embed.to_dict())
    assert "@everyone" not in encoded and "\ud800" not in encoded and "\u202e" not in encoded
    assert all("**_`" not in field.value for field in embed.fields)


def test_direct_renderer_input_is_capped_at_one_hundred_entries():
    entries = (entry(str(index), "other") for index in range(1000))
    assert page_count(entries, "other") == 10
    embed = render_help(tuple(entry(str(index), "other") for index in range(1000)), "other", 10)
    assert [field.name for field in embed.fields] == [f"/{i}" for i in range(90, 100)]


async def test_real_core_and_optional_music_have_complete_accurate_catalog():
    from core_v2.commands import Core
    from core_v2.music import Music
    from core_v2.music_options import MusicOptions

    bot = commands.Bot(command_prefix="!", help_command=None, intents=discord.Intents.none())
    bot.db = object()
    bot.settings = SimpleNamespace(ffmpeg="synthetic")
    try:
        await bot.add_cog(Core(bot))
        base = collect_commands(bot.tree, 123)
        assert len(base) == 18 and not any(item.name.startswith("music ") for item in base)
        assert page_count(base, "moderation") == 2
        assert {item.name for item in base if item.category == "rank"} == {"rank", "levels", "leaderboard"}
        await bot.add_cog(Music(bot, options=MusicOptions(), manager=SimpleNamespace(close=AsyncMock())))
        complete = collect_commands(bot.tree, 123)
        assert len(complete) == 26
        assert {item.name for item in complete if item.category == "music"} == {
            "music play", "music join", "music queue", "music skip", "music stop", "music autoplay",
            "music pause", "music resume"}
        assert "боту" not in next(item.permissions for item in complete if item.name == "modhistory")
        for category in available_categories(complete):
            for page in range(1, page_count(complete, category) + 1):
                assert_bounded(render_help(complete, category, page))
    finally:
        await bot.close()
