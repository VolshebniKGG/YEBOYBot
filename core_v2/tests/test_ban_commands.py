"""Ban/unban slash contracts without Discord login or mocked API success claims."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core_v2.commands import Core
from core_v2.moderation import KickResult, ModerationError, ModerationService
from core_v2.security import UserError


@pytest.fixture
def invocation():
    actor = MagicMock(spec=discord.Member)
    actor.id = 20
    interaction = SimpleNamespace(guild=SimpleNamespace(id=10), user=actor, id=40,
                                  response=SimpleNamespace(defer=AsyncMock()),
                                  followup=SimpleNamespace(send=AsyncMock()))
    cog = Core(SimpleNamespace(db=object()))
    cog.moderation = SimpleNamespace(ban=AsyncMock(), unban=AsyncMock())
    return cog, interaction


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_defer_precedes_service_and_request_is_bound_to_current_guild_actor_and_interaction(invocation, action):
    cog, interaction = invocation
    async def act(*args):
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        # No target Member object or cache lookup is needed for a former member.
        assert args == (10, 20, 99, 40, "Змістовна причина")
        return KickResult("a" * 32, "applied", "applied", "Discord підтвердив дію.")
    getattr(cog.moderation, action).side_effect = act
    await getattr(Core, action).callback(cog, interaction, "99", "Змістовна причина")
    args, kwargs = interaction.followup.send.await_args
    assert "a" * 32 in args[0] and "Застосовано" in args[0]
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict()["parse"] == []
    getattr(cog.moderation, "unban" if action == "ban" else "ban").assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
@pytest.mark.parametrize("identifier", [
    "@everyone", "<@99>", "-1", "1.0", "１２", "", "0", "000", " 99", "99 ",
    str(2**63), "9" * 20, pytest.param("9" * 5000, id="overlong-numeric-id"), None, 99,
])
async def test_invalid_id_never_reaches_service_or_followup(invocation, action, identifier):
    cog, interaction = invocation
    with pytest.raises(UserError):
        await getattr(Core, action).callback(cog, interaction, identifier, "Причина")
    cog.moderation.ban.assert_not_awaited()
    cog.moderation.unban.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_signed_bigint_upper_boundary_is_forwarded_without_rounding(invocation, action):
    cog, interaction = invocation
    getattr(cog.moderation, action).return_value = KickResult("a" * 32, "applied", "applied", "Збережено.")
    await getattr(Core, action).callback(cog, interaction, str(2**63 - 1), "Причина")
    assert getattr(cog.moderation, action).await_args.args[2] == 2**63 - 1


@pytest.mark.parametrize("action", ["ban", "unban"])
@pytest.mark.parametrize("invalid_context", ["dm", "not-a-member"])
async def test_server_context_is_required_before_defer_or_service(invocation, action, invalid_context):
    cog, interaction = invocation
    if invalid_context == "dm":
        interaction.guild = None
    else:
        interaction.user = SimpleNamespace(id=20)
    with pytest.raises(UserError, match="сервері"):
        await getattr(Core, action).callback(cog, interaction, "99", "Причина")
    interaction.response.defer.assert_not_awaited()
    cog.moderation.ban.assert_not_awaited()
    cog.moderation.unban.assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_safe_service_permission_error_uses_existing_command_error_handler(invocation, action):
    cog, interaction = invocation
    getattr(cog.moderation, action).side_effect = ModerationError("Боту потрібен дозвіл блокування.")
    with pytest.raises(UserError, match="дозвіл блокування"):
        await getattr(Core, action).callback(cog, interaction, "99", "Причина")
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_api_success_and_unconfirmed_case_update_never_become_failed_action(invocation, action):
    cog, interaction = invocation
    getattr(cog.moderation, action).return_value = KickResult(
        "a" * 32, "applied_unrecorded", message="Discord підтвердив дію; запис БД не підтверджено.")
    await getattr(Core, action).callback(cog, interaction, "99", "Причина")
    message = interaction.followup.send.await_args.args[0]
    assert "Discord підтвердив" in message and "Не виконано" not in message
    assert "Збережений стан" not in message and "a" * 32 in message


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_pending_duplicate_response_is_private_and_does_not_claim_application_retry(invocation, action):
    cog, interaction = invocation
    getattr(cog.moderation, action).return_value = KickResult(
        "a" * 32, "duplicate", "pending", "Запит уже зареєстровано. Повторної дії не надсилали.")
    await getattr(Core, action).callback(cog, interaction, "99", "Причина")
    args, kwargs = interaction.followup.send.await_args
    assert "Непідтверджено" in args[0] and "Повторної дії не надсилали" in args[0]
    assert "Застосовано" not in args[0]
    assert kwargs["ephemeral"] is True and kwargs["allowed_mentions"].to_dict()["parse"] == []
    getattr(cog.moderation, action).assert_awaited_once()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_failed_followup_does_not_dispatch_a_second_action(invocation, action):
    cog, interaction = invocation
    getattr(cog.moderation, action).return_value = KickResult("a" * 32, "applied", "applied", "Підтверджено.")
    interaction.followup.send.side_effect = RuntimeError("synthetic response failure")
    with pytest.raises(RuntimeError, match="response failure"):
        await getattr(Core, action).callback(cog, interaction, "99", "Причина")
    getattr(cog.moderation, action).assert_awaited_once()


@pytest.mark.parametrize("action", ["ban", "unban"])
def test_command_metadata_limits_input_and_requires_ban_permission(action):
    command = getattr(Core, action)
    assert command.guild_only and command.default_permissions.ban_members
    parameters = {parameter.name: parameter for parameter in command.parameters}
    assert set(parameters) == {"user_id", "reason"}
    assert parameters["user_id"].type is discord.AppCommandOptionType.string
    assert parameters["user_id"].min_value == 1 and parameters["user_id"].max_value == 19
    assert parameters["reason"].min_value == 1 and parameters["reason"].max_value == 500
    assert all(parameter.required for parameter in parameters.values())


def test_cog_binds_shared_moderation_service():
    database = object()
    cog = Core(SimpleNamespace(db=database))
    assert isinstance(cog.moderation, ModerationService)
    assert cog.moderation.db is database
