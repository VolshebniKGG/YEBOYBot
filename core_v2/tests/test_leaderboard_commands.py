"""Axis-specific leaderboard commands over real SQLite and offline SDK callbacks."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import app_commands
import pytest
from sqlalchemy import select

from core_v2 import commands as module
from core_v2.commands import Core
from core_v2.db import Database, xp
from core_v2.security import UserError

MAX = 2**63 - 1


def choice(kind):
    return app_commands.Choice(name="Untrusted label @everyone", value=kind)


@pytest.fixture
def invocation():
    guild = SimpleNamespace(id=10, unavailable=False)
    actor = MagicMock(spec=discord.Member)
    actor.id, actor.guild = 20, guild
    interaction = SimpleNamespace(guild=guild, guild_id=10, user=actor,
        response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
    database = SimpleNamespace(leaderboard=AsyncMock(return_value=[
        {"user_id": 30, "text_xp": 10, "voice_xp": 20}]))
    bot = SimpleNamespace(db=database, get_guild=MagicMock(return_value=guild))
    return Core(bot), interaction


async def invoke(fixture, page=1, xp_type=None):
    cog, interaction = fixture
    await Core.leaderboard.callback(cog, interaction, page, xp_type)


def sent(interaction):
    arguments = interaction.followup.send.await_args
    assert interaction.response.defer.await_count >= 1
    assert all(not call.args and not call.kwargs for call in interaction.response.defer.await_args_list)
    assert arguments.kwargs["allowed_mentions"].to_dict()["parse"] == []
    return arguments


@pytest.mark.parametrize("kind,expected,label", [
    (None, 30, "Загальний (чат + голос)"),
    ("total", 30, "Загальний (чат + голос)"),
    ("text", 10, "Чат"), ("voice", 20, "Голос")])
async def test_axes_preserve_default_page_call_and_publish_only_selected_counter(invocation, kind, expected, label):
    cog, interaction = invocation
    await invoke(invocation, 2, None if kind is None else choice(kind))
    interaction.response.defer.assert_awaited_once_with()
    if kind in {None, "total"}:
        cog.bot.db.leaderboard.assert_awaited_once_with(10, 2)
    else:
        cog.bot.db.leaderboard.assert_awaited_once_with(10, 2, kind=kind)
    message = sent(interaction)
    assert "ephemeral" not in message.kwargs
    embed = message.kwargs["embed"]
    assert embed.title == f"Рейтинг · {label} · 2"
    assert embed.description == f"11. <@30> — {expected} XP"
    assert "Untrusted label" not in str(embed.to_dict())
    assert "менший Discord ID вище" in embed.footer.text


@pytest.mark.parametrize("page", [0, -1, 1001, True, 1.0, "1", None])
async def test_direct_callback_rejects_invalid_page_without_defer_or_sql(invocation, page):
    cog, interaction = invocation
    with pytest.raises(UserError):
        await invoke(invocation, page)
    cog.bot.db.leaderboard.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize("xp_type", ["text", 1, choice("TEXT"), choice(""), choice(1), choice([])])
async def test_direct_callback_rejects_unknown_or_non_choice_axis(invocation, xp_type):
    cog, interaction = invocation
    with pytest.raises(UserError):
        await invoke(invocation, xp_type=xp_type)
    cog.bot.db.leaderboard.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()


@pytest.mark.parametrize("invalid", ["dm", "non-member", "wrong-guild-id", "non-integer-id", "unavailable",
                                        "different-canonical-guild", "removed-guild", "missing-guild-contract"])
async def test_invalid_context_fails_closed_before_query(invocation, invalid):
    cog, interaction = invocation
    if invalid == "dm":
        interaction.guild = None
    elif invalid == "non-member":
        interaction.user = SimpleNamespace(id=20)
    elif invalid == "wrong-guild-id":
        interaction.guild_id = 999
    elif invalid == "non-integer-id":
        interaction.guild.id = "10"
    elif invalid == "unavailable":
        interaction.guild.unavailable = True
    elif invalid == "different-canonical-guild":
        cog.bot.get_guild.return_value = SimpleNamespace(id=10, unavailable=False)
    elif invalid == "removed-guild":
        cog.bot.get_guild.return_value = None
    else:
        del cog.bot.get_guild
    with pytest.raises(UserError):
        await invoke(invocation)
    cog.bot.db.leaderboard.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize("change", ["remove", "unavailable", "replace", "interaction-scope"])
async def test_guild_loss_during_query_never_publishes_loaded_rows(invocation, change):
    cog, interaction = invocation
    async def query(*args, **kwargs):
        if change == "remove":
            cog.bot.get_guild.return_value = None
        elif change == "unavailable":
            interaction.guild.unavailable = True
        elif change == "replace":
            cog.bot.get_guild.return_value = SimpleNamespace(id=10, unavailable=False)
        else:
            interaction.guild_id = 999
        return [{"user_id": 30, "text_xp": 10, "voice_xp": 20}]
    cog.bot.db.leaderboard.side_effect = query
    await invoke(invocation)
    message = sent(interaction)
    assert "ephemeral" not in message.kwargs
    assert "embed" not in message.kwargs
    assert "30" not in message.args[0]


@pytest.mark.parametrize("error", [RuntimeError("postgresql://private:credential@host SQL select"),
                                   TimeoutError("private database details")])
async def test_database_error_returns_fixed_safe_public_message_and_safe_log(invocation, error, caplog):
    cog, interaction = invocation
    cog.bot.db.leaderboard.side_effect = error
    await invoke(invocation, xp_type=choice("voice"))
    message = sent(interaction)
    assert "ephemeral" not in message.kwargs
    assert "embed" not in message.kwargs
    text = str(message) + caplog.text
    assert str(error) not in text
    assert "credential" not in text and "postgresql://" not in text
    assert "Спробуйте пізніше" in message.args[0]
    cog.bot.db.leaderboard.assert_awaited_once_with(10, 1, kind="voice")


async def test_real_read_timeout_cancels_sql_without_retry(invocation, monkeypatch):
    cog, interaction = invocation
    cancelled = asyncio.Event()
    async def blocked(*args):
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
    cog.bot.db.leaderboard.side_effect = blocked
    monkeypatch.setattr(module, "LEADERBOARD_DB_TIMEOUT", .01)
    await invoke(invocation)
    assert cancelled.is_set()
    assert "ephemeral" not in sent(interaction).kwargs
    cog.bot.db.leaderboard.assert_awaited_once()


async def test_command_cancellation_propagates_without_false_error_response(invocation):
    cog, interaction = invocation
    entered = asyncio.Event()
    async def blocked(*args):
        entered.set()
        await asyncio.Future()
    cog.bot.db.leaderboard.side_effect = blocked
    task = asyncio.create_task(invoke(invocation))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    interaction.followup.send.assert_not_awaited()
    cog.bot.db.leaderboard.assert_awaited_once()


@pytest.mark.parametrize("kind", ["total", "text", "voice"])
async def test_empty_axis_result_is_bounded_public_message(invocation, kind):
    cog, interaction = invocation
    cog.bot.db.leaderboard.return_value = []
    await invoke(invocation, 1000, choice(kind))
    message = sent(interaction)
    assert "ephemeral" not in message.kwargs
    assert message.kwargs["embed"].description == "Поки немає XP."


async def test_embed_limits_and_exact_python_total_at_both_signed_64bit_maxima(invocation):
    cog, interaction = invocation
    rows = [{"user_id": MAX - i, "text_xp": MAX, "voice_xp": MAX,
             "display_name": "@everyone **malicious**", "guild_id": 10} for i in range(15)]
    original = deepcopy(rows)
    cog.bot.db.leaderboard.return_value = rows
    await invoke(invocation, 1000)
    embed = sent(interaction).kwargs["embed"]
    assert len(embed.description.splitlines()) == 10
    assert str(MAX * 2) in embed.description
    assert embed.description.startswith(f"9991. <@{MAX}> — {MAX * 2} XP")
    assert "10000." in embed.description
    assert "@everyone" not in str(embed.to_dict())
    assert "malicious" not in str(embed.to_dict())
    assert len(embed.description.encode("utf-16-le")) // 2 < 4096
    assert len(str(embed.to_dict()).encode("utf-16-le")) // 2 < 6000
    assert rows == original


@pytest.mark.parametrize("column,bad", [("user_id", "@everyone"), ("user_id", MAX + 1),
    ("user_id", True), ("text_xp", -1), ("voice_xp", "secret SQL"), ("text_xp", MAX + 1),
    ("voice_xp", 1.0)])
async def test_invalid_stored_values_never_enter_public_embed(invocation, column, bad):
    cog, interaction = invocation
    cog.bot.db.leaderboard.return_value[0][column] = bad
    await invoke(invocation)
    assert "ephemeral" not in sent(interaction).kwargs
    assert "embed" not in sent(interaction).kwargs


async def test_failed_followup_does_not_repeat_database_query(invocation):
    cog, interaction = invocation
    interaction.followup.send.side_effect = OSError("lost followup")
    with pytest.raises(OSError):
        await invoke(invocation)
    cog.bot.db.leaderboard.assert_awaited_once_with(10, 1)


def test_slash_metadata_keeps_page_default_and_exposes_three_localized_axes():
    parameters = {parameter.name: parameter for parameter in Core.leaderboard.parameters}
    assert set(parameters) == {"page", "xp_type"}
    assert parameters["page"].default == 1
    assert parameters["page"].min_value == 1 and parameters["page"].max_value == 1000
    assert parameters["xp_type"].required is False
    assert [(item.name, item.value) for item in parameters["xp_type"].choices] == [
        ("Загальний", "total"), ("Чат", "text"), ("Голос", "voice")]
    assert Core.leaderboard.guild_only is True


@pytest.fixture
async def sqlite_database(tmp_path):
    database = Database("sqlite+aiosqlite:///" + str(tmp_path / "рейтинг чату й голосу.db"))
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


async def seed(database, rows):
    async with database.engine.begin() as connection:
        await connection.execute(xp.insert(), rows)


async def all_rows(database):
    async with database.engine.connect() as connection:
        return [tuple(row) for row in await connection.execute(select(xp).order_by(xp.c.guild_id, xp.c.user_id))]


@pytest.mark.parametrize("kind,expected", [("text", [20, 40, 30]),
                                            ("voice", [30, 40, 20]),
                                            ("total", [30, 40, 20])])
async def test_real_sqlite_axis_order_matches_rank_card_and_preserves_other_guild(invocation, sqlite_database, kind, expected):
    cog, interaction = invocation
    await seed(sqlite_database, [
        {"guild_id": 10, "user_id": 20, "text_xp": 100, "voice_xp": 0},
        {"guild_id": 10, "user_id": 30, "text_xp": 0, "voice_xp": 200},
        {"guild_id": 10, "user_id": 40, "text_xp": 100, "voice_xp": 100},
        {"guild_id": 999, "user_id": 1, "text_xp": MAX, "voice_xp": MAX}])
    cog.bot.db = sqlite_database
    before = await all_rows(sqlite_database)
    await invoke(invocation, xp_type=choice(kind))
    embed = sent(interaction).kwargs["embed"]
    assert [int(line.split("<@")[1].split(">")[0]) for line in embed.description.splitlines()] == expected
    if kind != "total":
        for position, user_id in enumerate(expected, 1):
            snapshot = await sqlite_database.rank_snapshot(10, user_id)
            assert snapshot[f"{kind}_rank"] == position
    assert "<@1>" not in embed.description
    assert await all_rows(sqlite_database) == before


async def test_real_sqlite_total_orders_exactly_above_max_and_through_rounded_float_ties(invocation, sqlite_database):
    cog, interaction = invocation
    await seed(sqlite_database, [
        {"guild_id": 10, "user_id": 20, "text_xp": MAX, "voice_xp": MAX - 1},
        {"guild_id": 10, "user_id": 30, "text_xp": MAX, "voice_xp": MAX},
        {"guild_id": 10, "user_id": 40, "text_xp": MAX, "voice_xp": 0}])
    cog.bot.db = sqlite_database
    before = await all_rows(sqlite_database)
    await invoke(invocation)
    lines = sent(interaction).kwargs["embed"].description.splitlines()
    assert lines == [f"1. <@30> — {MAX * 2} XP", f"2. <@20> — {MAX * 2 - 1} XP",
                     f"3. <@40> — {MAX} XP"]
    assert await all_rows(sqlite_database) == before


@pytest.mark.parametrize("kind", ["total", "text", "voice"])
async def test_real_sqlite_tie_pages_stable_read_only_and_absolute_rank(invocation, sqlite_database, kind):
    cog, interaction = invocation
    await seed(sqlite_database, [{"guild_id": 10, "user_id": user_id, "text_xp": 5, "voice_xp": 5}
                                  for user_id in reversed(range(101, 113))])
    cog.bot.db = sqlite_database
    before = await all_rows(sqlite_database)
    outputs = []
    for _ in range(2):
        await invoke(invocation, 2, choice(kind))
        outputs.append(sent(interaction).kwargs["embed"].description)
    expected_xp = 10 if kind == "total" else 5
    assert outputs == [f"11. <@111> — {expected_xp} XP\n12. <@112> — {expected_xp} XP"] * 2
    if kind != "total":
        assert (await sqlite_database.rank_snapshot(10, 111))[f"{kind}_rank"] == 11
    assert await all_rows(sqlite_database) == before
