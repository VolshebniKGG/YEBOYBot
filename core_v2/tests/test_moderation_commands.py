"""Slash-command boundaries: acknowledgement, private output and stable service inputs."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core_v2.commands import Core
from core_v2.moderation import KickResult, ModerationError
from core_v2.security import UserError


@pytest.fixture
def invocation():
    actor = MagicMock(spec=discord.Member)
    actor.id = 20
    interaction = SimpleNamespace(guild=SimpleNamespace(id=10), user=actor, id=40,
                                  response=SimpleNamespace(defer=AsyncMock()),
                                  followup=SimpleNamespace(send=AsyncMock()))
    cog = Core(SimpleNamespace(db=object()))
    cog.moderation = SimpleNamespace(kick=AsyncMock(), history=AsyncMock())
    return cog, interaction


async def test_kick_defers_before_transport_and_binds_interaction_to_claim(invocation):
    cog, interaction = invocation
    async def kick(*args):
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        assert args == (10, 20, 30, 40, "Причина")
        return KickResult("a" * 32, "applied", "applied", "Підтверджено")
    cog.moderation.kick.side_effect = kick
    await Core.kick.callback(cog, interaction, SimpleNamespace(id=30), "Причина")
    args, kwargs = interaction.followup.send.await_args
    assert "a" * 32 in args[0] and "Застосовано" in args[0]
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict()["parse"] == []


async def test_api_success_with_audit_failure_is_not_reported_as_failed_kick(invocation):
    cog, interaction = invocation
    cog.moderation.kick.return_value = KickResult("a" * 32, "applied_unrecorded", message="Discord підтвердив kick; підсумок не збережено.")
    await Core.kick.callback(cog, interaction, SimpleNamespace(id=30), "Причина")
    message = interaction.followup.send.await_args.args[0]
    assert "підтвердив" in message and "Не виконано" not in message
    assert "Збережений стан" not in message


async def test_controlled_service_error_reaches_existing_error_handler(invocation):
    cog, interaction = invocation
    cog.moderation.kick.side_effect = ModerationError("Вам потрібен дозвіл")
    with pytest.raises(UserError, match="дозвіл"):
        await Core.kick.callback(cog, interaction, SimpleNamespace(id=30), "Причина")
    interaction.followup.send.assert_not_awaited()


async def test_history_accepts_former_user_id_and_keeps_reasons_private(invocation):
    cog, interaction = invocation
    cog.moderation.history.return_value = [dict(id="a" * 32, user_id=99, actor_id=20, action="kick",
        status="pending", reason="@everyone **причина**", created=100, updated=101)]
    state = discord.app_commands.Choice(name="Непідтверджено", value="pending")
    await Core.modhistory.callback(cog, interaction, user_id="99", state=state, page=2)
    cog.moderation.history.assert_awaited_once_with(10, 20, user_id=99, status="pending", page=2)
    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs["ephemeral"] is True and kwargs["allowed_mentions"].to_dict()["parse"] == []
    assert "Непідтверджено" in kwargs["embed"].fields[0].value
    assert "@everyone" not in kwargs["embed"].fields[0].value


@pytest.mark.parametrize("identifier", ["@everyone", "-1", "12.0", "１２", "",
                                        "9" * 20, pytest.param("9" * 5000, id="overlong-numeric-id")])
async def test_bad_history_identifier_does_not_reach_service(invocation, identifier):
    cog, interaction = invocation
    with pytest.raises(UserError):
        await Core.modhistory.callback(cog, interaction, user_id=identifier)
    cog.moderation.history.assert_not_awaited()


@pytest.mark.parametrize("name", ["kick", "modhistory"])
async def test_dm_is_refused_before_service_or_defer(invocation, name):
    cog, interaction = invocation
    interaction.guild = None
    callback = getattr(Core, name).callback
    with pytest.raises(UserError):
        if name == "kick":
            await callback(cog, interaction, SimpleNamespace(id=30), "Причина")
        else:
            await callback(cog, interaction)
    interaction.response.defer.assert_not_awaited()
    cog.moderation.kick.assert_not_awaited()
    cog.moderation.history.assert_not_awaited()


def test_history_five_long_emoji_reasons_fit_utf16_field_and_message_limits():
    from core_v2.discord_moderation import history_embed
    rows = [dict(id=f"{i:032x}", user_id=2**63-1, actor_id=2**63-1, action="kick", status="pending",
                 reason="😀" * 500, created=1700000000, updated=1700000001) for i in range(5)]
    embed = history_embed(rows, 1000)
    def units(value):
        return len(value.encode("utf-16-le")) // 2
    assert len(embed.fields) == 5
    assert all(units(field.value) <= 1024 for field in embed.fields)
    assert sum(units(field.name) + units(field.value) for field in embed.fields) + units(embed.title) + units(embed.description) <= 6000
    assert all("…" in field.value for field in embed.fields)
    assert all(row["reason"] == "😀" * 500 for row in rows)


def test_old_failed_case_does_not_claim_discord_action_was_cancelled():
    from core_v2.discord_moderation import history_embed
    row = dict(id="a"*32, user_id=30, actor_id=20, action="timeout", status="failed", reason="Причина", created=100, updated=101)
    embed = history_embed([row], 1)
    assert "Зафіксовано помилку" in embed.fields[0].value
    assert "Не виконано" not in embed.fields[0].value
    assert "не доводить" in embed.description
