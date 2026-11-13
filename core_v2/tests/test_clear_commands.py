"""Clear preview/button contracts without a Discord login or message deletion."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import time

import discord
import pytest

from core_v2.clear_commands import ClearConfirmation, preview, status
from core_v2.commands import Core
from core_v2.moderation import ModerationError
from core_v2.security import UserError

REQUEST = "000000000000000a0000000000000028"


def interaction(*, guild_id=10, actor_id=20, channel_id=30, member=True):
    user = MagicMock(spec=discord.Member) if member else SimpleNamespace()
    user.id = actor_id
    response = SimpleNamespace(is_done=MagicMock(return_value=False), send_message=AsyncMock())

    async def defer(**kwargs):
        response.is_done.return_value = True

    response.defer = AsyncMock(side_effect=defer)
    return SimpleNamespace(guild=None if guild_id is None else SimpleNamespace(id=guild_id),
                           user=user, channel_id=channel_id, id=40, response=response,
                           followup=SimpleNamespace(send=AsyncMock()))


def result(state="clear_done", code="done", count=3, message=""):
    return SimpleNamespace(id=REQUEST, state=state, code=code, count=count, message=message)


@pytest.fixture
def invocation():
    plan = SimpleNamespace(id=REQUEST, guild_id=10, actor_id=20, channel_id=30, request_id=40,
                           reason="Причина @everyone **перевірки**", requested=5,
                           message_ids=(35, 34, 33), expires=time.time() + 60,
                           skipped_pinned=1, skipped_old=1, skipped_system=0)
    record = {"plan": plan, "state": "clear_confirm"}
    message = SimpleNamespace(edit=AsyncMock())
    command = interaction()
    command.followup.send.return_value = message
    service = SimpleNamespace(prepare=AsyncMock(return_value=record),
                              execute=AsyncMock(return_value=result()),
                              cancel=AsyncMock(return_value=result("clear_cancelled", "cancelled", 0)),
                              status=AsyncMock(return_value=record))
    return SimpleNamespace(command=command, plan=plan, record=record, message=message, service=service)


def assert_private(kwargs):
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict()["parse"] == []


async def shown_view(fixture):
    await preview(fixture.command, fixture.service, 5, fixture.plan.reason)
    return fixture.command.followup.send.await_args.kwargs["view"]


async def test_preview_defers_before_preparing_and_does_not_execute(invocation):
    f = invocation

    async def prepare(*arguments):
        f.command.response.defer.assert_awaited_once_with(ephemeral=True)
        assert arguments == (10, 20, 30, 40, 5, f.plan.reason)
        return f.record

    f.service.prepare.side_effect = prepare
    view = await shown_view(f)
    assert isinstance(view, ClearConfirmation) and view.timeout == 60
    assert (view.guild_id, view.actor_id, view.channel_id, view.request_id) == (10, 20, 30, REQUEST)
    assert not hasattr(view, "plan")
    assert view.message is f.message
    kwargs = f.command.followup.send.await_args.kwargs
    assert_private(kwargs)
    assert kwargs["wait"] is True
    embed = kwargs["embed"]
    assert len(embed) <= 6000
    assert "незворотне" in embed.description
    text = "\n".join(field.value for field in embed.fields)
    assert "**3**" in text and "**5**" in text
    assert "@everyone" not in text and "@\u200beveryone" in text
    assert "\\*\\*перевірки\\*\\*" in text
    assert REQUEST in text and "25" in text and "7 діб" in text
    f.service.execute.assert_not_awaited()
    f.service.cancel.assert_not_awaited()
    view.stop()


@pytest.mark.parametrize("helper", ["preview", "status"])
@pytest.mark.parametrize("context", ["dm", "not-member"])
async def test_command_context_is_required_before_defer_or_service(invocation, helper, context):
    f = invocation
    command = interaction(guild_id=None) if context == "dm" else interaction(member=False)
    with pytest.raises(UserError, match="сервері"):
        if helper == "preview":
            await preview(command, f.service, 5, "Причина")
        else:
            await status(command, f.service, REQUEST)
    command.response.defer.assert_not_awaited()
    f.service.prepare.assert_not_awaited()
    f.service.status.assert_not_awaited()


@pytest.mark.parametrize("state", ["clear_running", "clear_pending", "clear_done",
                                    "clear_cancelled", "clear_expired", "clear_failed"])
async def test_duplicate_preview_never_creates_another_confirmation_for_nonconfirm_state(invocation, state):
    f = invocation
    f.record["state"] = state
    await preview(f.command, f.service, 5, f.plan.reason)
    kwargs = f.command.followup.send.await_args.kwargs
    assert kwargs["view"] is None
    assert_private(kwargs)
    f.service.execute.assert_not_awaited()


@pytest.mark.parametrize("helper", ["preview", "status"])
async def test_controlled_command_error_reaches_existing_error_handler(invocation, helper):
    f = invocation
    getattr(f.service, "prepare" if helper == "preview" else "status").side_effect = ModerationError("Немає доступу.")
    with pytest.raises(UserError, match="Немає доступу"):
        if helper == "preview":
            await preview(f.command, f.service, 5, "Причина")
        else:
            await status(f.command, f.service, REQUEST)
    f.command.followup.send.assert_not_awaited()
    f.service.execute.assert_not_awaited()


@pytest.mark.parametrize("helper", ["preview", "status"])
async def test_unexpected_command_errors_do_not_expose_private_payload(invocation, helper, caplog):
    f = invocation
    getattr(f.service, "prepare" if helper == "preview" else "status").side_effect = RuntimeError("private-storage-URL")
    with pytest.raises(UserError) as error:
        if helper == "preview":
            await preview(f.command, f.service, 5, "Причина")
        else:
            await status(f.command, f.service, REQUEST)
    assert "private-storage-URL" not in str(error.value) + caplog.text
    f.service.execute.assert_not_awaited()


@pytest.mark.parametrize("change", [{"actor_id": 21}, {"guild_id": 11}, {"channel_id": 31},
                                    {"guild_id": None}, {"member": False}])
async def test_foreign_button_click_never_consumes_or_calls_service(invocation, change):
    f = invocation
    view = await shown_view(f)
    click = interaction(**change)
    await view.confirm.callback(click)
    f.service.execute.assert_not_awaited()
    f.service.cancel.assert_not_awaited()
    assert not view._consumed
    assert all(not item.disabled for item in view.children)
    click.response.defer.assert_not_awaited()
    assert_private(click.response.send_message.await_args.kwargs)
    view.stop()


@pytest.mark.parametrize("action", ["execute", "cancel"])
async def test_owned_button_defers_and_uses_only_bound_scope_and_request_id(invocation, action):
    f = invocation
    view = await shown_view(f)
    click = interaction()

    async def act(*arguments):
        click.response.defer.assert_awaited_once_with(ephemeral=True)
        assert arguments == (10, 20, 30, REQUEST)
        return result() if action == "execute" else result("clear_cancelled", "cancelled", 0)

    getattr(f.service, action).side_effect = act
    # A mutable UI fixture cannot replace the server's canonical selection.
    f.plan.message_ids = (999,)
    f.plan.reason = "Changed UI data"
    await (view.confirm if action == "execute" else view.cancel).callback(click)
    getattr(f.service, action).assert_awaited_once()
    assert view.is_finished() and all(item.disabled for item in view.children)
    assert_private(click.followup.send.await_args.kwargs)
    f.message.edit.assert_awaited_once()
    assert f.message.edit.await_args.kwargs["view"] is view


async def test_concurrent_confirm_and_cancel_do_not_dispatch_two_actions(invocation):
    f = invocation
    view = await shown_view(f)
    started, release = asyncio.Event(), asyncio.Event()

    async def execute(*arguments):
        started.set()
        await release.wait()
        return result()

    f.service.execute.side_effect = execute
    first, second = interaction(), interaction()
    operation = asyncio.create_task(view.confirm.callback(first))
    await asyncio.wait_for(started.wait(), 1)
    duplicate = asyncio.create_task(view.cancel.callback(second))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(operation, duplicate)
    f.service.execute.assert_awaited_once()
    f.service.cancel.assert_not_awaited()
    assert "вже використано" in second.followup.send.await_args.args[0]
    assert_private(second.followup.send.await_args.kwargs)


async def test_cancel_then_confirm_never_executes(invocation):
    f = invocation
    view = await shown_view(f)
    await view.cancel.callback(interaction())
    duplicate = interaction()
    await view.confirm.callback(duplicate)
    f.service.cancel.assert_awaited_once()
    f.service.execute.assert_not_awaited()
    assert "вже використано" in duplicate.followup.send.await_args.args[0]


@pytest.mark.parametrize("state", ["clear_running", "clear_pending", "clear_expired", "clear_failed", "clear_cancelled"])
async def test_uncertain_or_not_sent_outcome_does_not_claim_confirmed_deletion(invocation, state):
    f = invocation
    view = await shown_view(f)
    f.service.execute.return_value = result(state, "duplicate", 3)
    click = interaction()
    await view.confirm.callback(click)
    text = click.followup.send.await_args.args[0]
    assert "Discord підтвердив" not in text
    assert REQUEST in text
    if state in {"clear_running", "clear_pending"}:
        assert "не підтверджений" in text


async def test_sdk_success_with_unknown_database_result_is_reported_separately(invocation):
    f = invocation
    view = await shown_view(f)
    f.service.execute.return_value = result(None, "api_confirmed_db_unknown", 3)
    click = interaction()
    await view.confirm.callback(click)
    text = click.followup.send.await_args.args[0]
    assert "Discord підтвердив запит для 3" in text
    assert "збереження результату не підтверджено" in text
    assert "Зупинено до надсилання" not in text


async def test_completed_duplicate_without_count_does_not_invent_zero_deletions(invocation):
    f = invocation
    view = await shown_view(f)
    f.service.execute.return_value = result("clear_done", "duplicate", 0)
    click = interaction()
    await view.confirm.callback(click)
    text = click.followup.send.await_args.args[0]
    assert "Збережений запит підтверджено Discord" in text
    assert "Повторного видалення не запускали" in text
    assert "для 0 повідомлень" not in text
    f.service.execute.assert_awaited_once()


async def test_controlled_button_permission_failure_is_private_and_not_retried(invocation):
    f = invocation
    view = await shown_view(f)
    f.service.execute.side_effect = ModerationError("Боту потрібен дозвіл керування повідомленнями.")
    click = interaction()
    await view.confirm.callback(click)
    assert "дозвіл керування" in click.followup.send.await_args.args[0]
    assert_private(click.followup.send.await_args.kwargs)
    await view.confirm.callback(interaction())
    f.service.execute.assert_awaited_once()


async def test_unknown_button_failure_is_safe_without_claiming_no_deletion(invocation, caplog):
    f = invocation
    view = await shown_view(f)
    f.service.execute.side_effect = RuntimeError("private-delete-payload")
    click = interaction()
    await view.confirm.callback(click)
    text = click.followup.send.await_args.args[0]
    assert "private-delete-payload" not in text + caplog.text
    assert "не вдалося" in text.lower() and "Не повторюйте" in text
    assert "не надсилали" not in text
    f.service.execute.assert_awaited_once()


async def test_cancellation_propagates_but_cannot_reactivate_confirmation(invocation):
    f = invocation
    view = await shown_view(f)
    f.service.execute.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await view.confirm.callback(interaction())
    assert view.is_finished() and all(item.disabled for item in view.children)
    await view.confirm.callback(interaction())
    f.service.execute.assert_awaited_once()


async def test_failed_followup_does_not_retry_remote_action(invocation):
    f = invocation
    view = await shown_view(f)
    click = interaction()
    click.followup.send.side_effect = RuntimeError("response-failed")
    with pytest.raises(RuntimeError, match="response-failed"):
        await view.confirm.callback(click)
    await view.confirm.callback(interaction())
    f.service.execute.assert_awaited_once()


async def test_timeout_only_disables_original_view_without_service_mutation(invocation):
    f = invocation
    view = await shown_view(f)
    await view.on_timeout()
    f.service.execute.assert_not_awaited()
    f.service.cancel.assert_not_awaited()
    assert view.is_finished() and all(item.disabled for item in view.children)
    f.message.edit.assert_awaited_once()
    await view.confirm.callback(interaction())
    f.service.execute.assert_not_awaited()


async def test_timeout_edit_failure_is_safe_and_does_not_mutate(invocation, caplog):
    f = invocation
    view = await shown_view(f)
    f.message.edit.side_effect = RuntimeError("private-edit-payload")
    await view.on_timeout()
    assert "private-edit-payload" not in caplog.text
    f.service.execute.assert_not_awaited()
    f.service.cancel.assert_not_awaited()


async def test_component_error_handler_does_not_log_exception_or_response_payloads(invocation, caplog):
    f = invocation
    view = await shown_view(f)
    click = interaction()
    click.response.send_message.side_effect = RuntimeError("private-error-response")
    await view.on_error(click, RuntimeError("private-callback-payload"), view.confirm)
    assert "private-callback-payload" not in caplog.text
    assert "private-error-response" not in caplog.text
    f.service.execute.assert_not_awaited()
    view.stop()


@pytest.mark.parametrize("identifier", ["", "a" * 31, "A" * 32, "g" * 32,
                                        pytest.param("a" * 5000, id="overlong-request"), None, 10, {}])
async def test_bad_status_request_id_never_reaches_service(invocation, identifier):
    f = invocation
    with pytest.raises(UserError):
        await status(f.command, f.service, identifier)
    f.command.response.defer.assert_awaited_once_with(ephemeral=True)
    f.service.status.assert_not_awaited()
    f.command.followup.send.assert_not_awaited()


@pytest.mark.parametrize("state", ["clear_confirm", "clear_running", "clear_pending", "clear_done",
                                    "clear_cancelled", "clear_expired", "clear_failed"])
async def test_status_is_bound_private_and_never_dispatches_an_action(invocation, state):
    f = invocation
    f.record["state"] = state
    await status(f.command, f.service, REQUEST)
    f.service.status.assert_awaited_once_with(10, 20, 30, REQUEST)
    kwargs = f.command.followup.send.await_args.kwargs
    assert_private(kwargs)
    embed = kwargs["embed"]
    text = embed.description + "\n" + "\n".join(field.value for field in embed.fields)
    assert ("Discord підтвердив запит для" in text) is (state == "clear_done")
    assert REQUEST in text
    f.service.execute.assert_not_awaited()
    f.service.cancel.assert_not_awaited()


async def test_long_emoji_and_markdown_reason_stays_within_utf16_embed_limits(invocation):
    f = invocation
    f.plan.reason = "😀*" * 100
    await status(f.command, f.service, REQUEST)
    embed = f.command.followup.send.await_args.kwargs["embed"]
    def units(value):
        return len(value.encode("utf-16-le")) // 2
    assert all(units(field.value) <= 1024 and units(field.name) <= 256 for field in embed.fields)
    assert sum(units(field.name) + units(field.value) for field in embed.fields) + units(embed.title) + units(embed.description) <= 6000
    assert f.plan.reason == "😀*" * 100


async def test_registered_clear_wrapper_routes_to_private_preview(invocation):
    f = invocation
    cog = Core(SimpleNamespace(db=object()))
    cog._clear_service = f.service
    await Core.clear.callback(cog, f.command, 5, f.plan.reason)
    f.service.prepare.assert_awaited_once_with(10, 20, 30, 40, 5, f.plan.reason)
    kwargs = f.command.followup.send.await_args.kwargs
    assert_private(kwargs)
    kwargs["view"].stop()


async def test_registered_status_wrapper_routes_only_owner_scoped_read(invocation):
    f = invocation
    cog = Core(SimpleNamespace(db=object()))
    cog._clear_service = f.service
    await Core.clear_status.callback(cog, f.command, REQUEST)
    f.service.status.assert_awaited_once_with(10, 20, 30, REQUEST)
    f.service.execute.assert_not_awaited()
    assert_private(f.command.followup.send.await_args.kwargs)


@pytest.mark.parametrize("command", ["clear", "clear_status"])
async def test_registered_command_rejects_dm_before_constructing_domain(invocation, command):
    f = invocation
    cog = Core(SimpleNamespace(db=object()))
    cog._clear_domain = MagicMock(side_effect=AssertionError("Domain must not be initialized in DM"))
    with pytest.raises(UserError):
        if command == "clear":
            await Core.clear.callback(cog, interaction(guild_id=None), 5, "Причина")
        else:
            await Core.clear_status.callback(cog, interaction(guild_id=None), REQUEST)
    cog._clear_domain.assert_not_called()
    f.service.execute.assert_not_awaited()


def test_registered_clear_metadata_bounds_count_reason_and_confirmation_receipt():
    command = Core.clear
    assert command.guild_only and command.default_permissions.manage_messages
    parameters = {parameter.name: parameter for parameter in command.parameters}
    assert set(parameters) == {"amount", "reason"}
    assert parameters["amount"].min_value == 1 and parameters["amount"].max_value == 25
    assert parameters["reason"].min_value == 1 and parameters["reason"].max_value == 200
    receipt = Core.clear_status
    assert receipt.guild_only
    # Reading one's own receipt does not depend on bot/moderation permission retention.
    assert receipt.default_permissions is None
    assert receipt.parameters[0].min_value == receipt.parameters[0].max_value == 32
