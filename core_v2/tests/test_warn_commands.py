"""Warning slash-command UX and bounded private output, without Discord login."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core_v2 import commands as module
from core_v2.commands import Core
from core_v2.discord_moderation import warning_embeds
from core_v2.moderation import ModerationError, ModerationResult
from core_v2.security import UserError


CASE = "000000000000000a0000000000000028"


@pytest.fixture
def invocation():
    actor = MagicMock(spec=discord.Member)
    actor.id = 20
    actor.guild_permissions = discord.Permissions(moderate_members=True)
    target = MagicMock(spec=discord.Member)
    target.id = 30
    interaction = SimpleNamespace(guild=SimpleNamespace(id=10), guild_id=10, user=actor, id=40,
                                  response=SimpleNamespace(defer=AsyncMock()),
                                  followup=SimpleNamespace(send=AsyncMock()))
    database = SimpleNamespace(create_case=AsyncMock(side_effect=AssertionError("Old UUID write")),
                               record_warning=AsyncMock(side_effect=AssertionError("Direct SQL from command")),
                               warnings=AsyncMock(return_value=[]))
    cog = Core(SimpleNamespace(db=database))
    cog.checked_target = AsyncMock(side_effect=AssertionError("Cached authorization path"))
    cog.moderation = SimpleNamespace(warn=AsyncMock(return_value=ModerationResult(
        CASE, "applied", "applied", "Попередження збережено.")))
    return cog, interaction, target


async def invoke_warn(fixture, reason="Причина попередження"):
    cog, interaction, target = fixture
    await Core.warn.callback(cog, interaction, target, reason)


def private_send(interaction):
    arguments = interaction.followup.send.await_args
    assert arguments.kwargs["ephemeral"] is True
    assert arguments.kwargs["allowed_mentions"].to_dict()["parse"] == []
    return arguments


async def test_warn_acknowledges_before_service_and_binds_ids_without_direct_sql(invocation):
    cog, interaction, target = invocation

    async def record(*args):
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        assert args == (10, 20, 30, 40, "  Повна причина  ")
        return ModerationResult(CASE, "applied", "applied", "Попередження збережено.")

    cog.moderation.warn.side_effect = record
    await invoke_warn(invocation, "  Повна причина  ")
    assert CASE in private_send(interaction).args[0]
    cog.checked_target.assert_not_awaited()
    cog.bot.db.create_case.assert_not_awaited()
    cog.bot.db.record_warning.assert_not_awaited()


async def test_warn_does_not_use_stale_actor_permissions_or_cached_guild_me(invocation):
    cog, interaction, target = invocation
    interaction.user.guild_permissions = discord.Permissions.none()
    interaction.guild.me = None
    await invoke_warn(invocation)
    cog.moderation.warn.assert_awaited_once_with(10, 20, 30, 40, "Причина попередження")
    assert "збережено" in private_send(interaction).args[0]


@pytest.mark.parametrize("invalid_context", ["dm", "non-member"])
async def test_warn_rejects_invalid_context_before_acknowledgement_or_service(invocation, invalid_context):
    cog, interaction, target = invocation
    if invalid_context == "dm":
        interaction.guild = None
    else:
        interaction.user = SimpleNamespace(id=20)
    with pytest.raises(UserError):
        await invoke_warn(invocation)
    interaction.response.defer.assert_not_awaited()
    cog.moderation.warn.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


async def test_warn_controlled_error_uses_existing_user_error_boundary(invocation):
    cog, interaction, target = invocation
    cog.moderation.warn.side_effect = ModerationError("Роль учасника має бути нижчою за вашу.")
    with pytest.raises(UserError, match="Роль учасника"):
        await invoke_warn(invocation)
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_not_awaited()


async def test_unconfirmed_database_write_is_not_reported_as_applied_or_failed(invocation):
    cog, interaction, target = invocation
    cog.moderation.warn.return_value = ModerationResult(
        CASE, "unconfirmed", message="Запис не підтверджено. Перевірте /modhistory і /warnings.")
    await invoke_warn(invocation)
    text = private_send(interaction).args[0]
    assert CASE in text and "не підтверджено" in text
    assert "Застосовано" not in text and "Зафіксовано помилку" not in text
    assert "Збережений стан" not in text


@pytest.mark.parametrize("state,label", [("revoked", "Відкликано"), ("pending", "Непідтверджено"),
                                        ("failed", "Зафіксовано помилку"), ("applied", "Застосовано")])
async def test_duplicate_warn_reports_actual_saved_state(invocation, state, label):
    cog, interaction, target = invocation
    cog.moderation.warn.return_value = ModerationResult(
        CASE, "duplicate", state, "Цей запит уже зареєстровано. Повторного попередження немає.")
    await invoke_warn(invocation)
    text = private_send(interaction).args[0]
    assert f"Збережений стан: {label}" in text and CASE in text
    if state != "applied":
        assert "Застосовано" not in text


async def test_lost_followup_does_not_repeat_service_or_insert(invocation):
    cog, interaction, target = invocation
    interaction.followup.send.side_effect = RuntimeError("Synthetic lost response")
    with pytest.raises(RuntimeError, match="lost response"):
        await invoke_warn(invocation)
    cog.moderation.warn.assert_awaited_once()
    cog.bot.db.create_case.assert_not_awaited()
    cog.bot.db.record_warning.assert_not_awaited()


def test_warn_registration_keeps_guild_only_permission_and_reason_limits():
    command = Core.warn
    assert command.guild_only is True
    assert command.default_permissions.moderate_members
    reason = next(parameter for parameter in command.parameters if parameter.name == "reason")
    assert reason.min_value == 1 and reason.max_value == 500
    assert len(Core.__cog_app_commands__) == 18


async def test_warnings_defers_before_scoped_database_read_and_sends_private_output(invocation):
    cog, interaction, target = invocation

    async def read(*args):
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        assert args == (10, 30)
        return [{"id": CASE, "reason": "Причина"}]

    cog.bot.db.warnings.side_effect = read
    await Core.warnings.callback(cog, interaction, target)
    output = private_send(interaction)
    assert output.kwargs["embeds"][0].title == CASE
    assert output.kwargs["embeds"][0].description == "Причина"


async def test_empty_warnings_are_private_with_no_mentions(invocation):
    cog, interaction, target = invocation
    await Core.warnings.callback(cog, interaction, target)
    assert private_send(interaction).args[0] == "Попереджень немає."


@pytest.mark.parametrize("invalid_context", ["dm", "non-member", "no-permission"])
async def test_warnings_existing_access_guard_refuses_before_database(invocation, invalid_context):
    cog, interaction, target = invocation
    if invalid_context == "dm":
        interaction.guild = None
    elif invalid_context == "non-member":
        interaction.user = SimpleNamespace(id=20)
    else:
        interaction.user.guild_permissions = discord.Permissions.none()
    with pytest.raises(UserError):
        await Core.warnings.callback(cog, interaction, target)
    interaction.response.defer.assert_not_awaited()
    cog.bot.db.warnings.assert_not_awaited()


@pytest.mark.parametrize("error", [RuntimeError("private SQL/password"), TimeoutError("private DB URL")],
                         ids=["database-error", "database-timeout"])
async def test_warnings_database_failure_is_safe_and_does_not_send_rows(invocation, error):
    cog, interaction, target = invocation
    cog.bot.db.warnings.side_effect = error
    with pytest.raises(UserError) as caught:
        await Core.warnings.callback(cog, interaction, target)
    assert "private" not in str(caught.value)
    assert "БД" in str(caught.value)
    interaction.followup.send.assert_not_awaited()


async def test_warnings_actual_timeout_bounds_a_stalled_database_call(invocation, monkeypatch):
    cog, interaction, target = invocation
    actual_timeout = asyncio.timeout
    monkeypatch.setattr(module.asyncio, "timeout", lambda seconds: actual_timeout(0))

    async def stalled(*args):
        await asyncio.Future()

    cog.bot.db.warnings.side_effect = stalled
    with pytest.raises(UserError, match="БД"):
        await Core.warnings.callback(cog, interaction, target)
    interaction.followup.send.assert_not_awaited()


async def test_warnings_cancellation_propagates_without_retry_or_output(invocation):
    cog, interaction, target = invocation
    cog.bot.db.warnings.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await Core.warnings.callback(cog, interaction, target)
    cog.bot.db.warnings.assert_awaited_once()
    interaction.followup.send.assert_not_awaited()


def utf16(value):
    return len(value.encode("utf-16-le")) // 2


@pytest.mark.parametrize("reason", ["😀" * 500, "**_~`" * 100, "@everyone @here <@123> " * 20],
                         ids=["emoji", "markdown", "mentions"])
async def test_ten_long_reasons_fit_discord_aggregate_budget_without_changing_storage(invocation, reason):
    cog, interaction, target = invocation
    rows = [{"id": f"{number:032x}", "reason": reason} for number in range(10)]
    before = deepcopy(rows)
    cog.bot.db.warnings.return_value = rows
    await Core.warnings.callback(cog, interaction, target)
    embeds = private_send(interaction).kwargs["embeds"]
    assert len(embeds) == 10
    assert sum(utf16(embed.title) + utf16(embed.description) + utf16(embed.footer.text)
               for embed in embeds) <= 6000
    assert all(utf16(embed.description) <= 4096 and utf16(embed.title) <= 256 for embed in embeds)
    assert all("/modhistory" in embed.footer.text for embed in embeds)
    assert rows == before
    if reason.startswith("**"):
        assert all("\\*" in embed.description for embed in embeds)
    if "@everyone" in reason:
        assert all("@everyone" not in embed.description for embed in embeds)


def test_warning_renderer_bounds_unexpected_extra_rows_and_long_titles():
    rows = [{"id": "😀" * 500, "reason": "😀" * 500} for _ in range(11)]
    before = deepcopy(rows)
    embeds = warning_embeds(rows)
    assert len(embeds) == 10
    assert all(utf16(embed.title) <= 256 for embed in embeds)
    assert sum(utf16(embed.title) + utf16(embed.description) + utf16(embed.footer.text)
               for embed in embeds) <= 6000
    assert rows == before
