"""SQLite → moderation service → real discord.py SDK, with offline HTTP only."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core_v2.db import Database
from core_v2.moderation import ModerationService
from test_ban_gateway import api_error, fail_member
from test_discord_moderation import (
    ACTOR, ACTOR_ROLE, BOT, GUILD, TARGET,
    rest_snapshot as rest_snapshot, user_payload,
)

REQUEST = 800
REASON = "Обґрунтована причина модерації"


@pytest.fixture
async def integrated(rest_snapshot, tmp_path, monkeypatch):
    state = rest_snapshot
    permission = str(discord.Permissions(ban_members=True).value)
    state.fresh["roles"][1]["permissions"] = permission
    state.fresh["roles"][2]["permissions"] = permission
    state.ban, state.unban = AsyncMock(), AsyncMock()
    state.get_ban = AsyncMock(return_value={"user": user_payload(TARGET), "reason": "Попередня причина"})
    monkeypatch.setattr(state.client.http, "ban", state.ban)
    monkeypatch.setattr(state.client.http, "unban", state.unban)
    monkeypatch.setattr(state.client.http, "get_ban", state.get_ban)
    database = Database("sqlite+aiosqlite:///" + str(tmp_path / "Інтеграція з пробілами.db"))
    try:
        await database.initialize()
        yield SimpleNamespace(state=state, database=database,
                              service=ModerationService(database, state.gateway))
    finally:
        await database.close()


async def invoke(fixture, action):
    return await getattr(fixture.service, action)(GUILD, ACTOR, TARGET, REQUEST, REASON)


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_real_sql_claim_precedes_sdk_mutation_and_success_reaches_history(integrated, action):
    fixture, state = integrated, integrated.state

    async def mutate(*arguments, reason):
        rows = await fixture.database.case_history(GUILD)
        assert len(rows) == 1 and rows[0]["status"] == "pending"
        assert rows[0]["action"] == action and rows[0]["reason"] == REASON
        assert rows[0]["id"] in reason and str(ACTOR) in reason
        assert arguments == ((TARGET, GUILD, 0) if action == "ban" else (TARGET, GUILD))

    getattr(state, action).side_effect = mutate
    result = await invoke(fixture, action)
    assert result.code == "applied" and result.status == "applied"
    rows = await fixture.service.history(GUILD, ACTOR, user_id=TARGET, status="applied")
    assert len(rows) == 1 and rows[0]["id"] == result.case_id
    assert rows[0]["updated"] >= rows[0]["created"]
    assert rows[0]["guild_id"] == GUILD and rows[0]["action"] == action
    assert await fixture.database.case_history(GUILD + 1) == []
    # Cached role state stays stale; success must depend on fresh REST snapshots.
    assert not state.cached.get_role(ACTOR_ROLE).permissions.ban_members
    assert len(state.get_guild.await_args_list) == 3  # authorization, preflight, history


async def test_absent_member_ban_uses_precise_http_read_and_keeps_messages(integrated):
    fixture, state = integrated, integrated.state
    fail_member(state, TARGET, api_error(404, 10007))
    result = await invoke(fixture, "ban")
    assert result.code == "applied"
    assert state.ban.await_args.args == (TARGET, GUILD, 0)
    row = (await fixture.database.case_history(GUILD))[0]
    assert row["action"] == "ban" and row["status"] == "applied"
    assert row["user_id"] == TARGET


@pytest.mark.parametrize("code", [10004, 10013, 0])
async def test_unexpected_target_404_is_saved_as_not_sent_before_ban(integrated, code):
    fixture, state = integrated, integrated.state
    fail_member(state, TARGET, api_error(404, code))
    result = await invoke(fixture, "ban")
    assert result.code == "not_sent" and result.status == "failed"
    state.ban.assert_not_awaited()
    assert (await fixture.database.case_history(GUILD))[0]["status"] == "failed"
    assert "private-api-payload" not in result.message


async def test_unban_of_former_member_uses_only_actual_ban_record(integrated):
    fixture, state = integrated, integrated.state
    del state.members[TARGET]
    result = await invoke(fixture, "unban")
    assert result.code == "applied"
    state.get_ban.assert_awaited_once_with(TARGET, GUILD)
    assert {call.args[1] for call in state.get_member.await_args_list} == {ACTOR, BOT}
    assert state.unban.await_args.args == (TARGET, GUILD)
    assert (await fixture.database.case_history(GUILD))[0]["action"] == "unban"


async def test_known_missing_ban_is_recorded_without_remote_unban(integrated):
    fixture, state = integrated, integrated.state
    state.get_ban.side_effect = api_error(404, 10026)
    result = await invoke(fixture, "unban")
    assert result.code == "not_sent" and result.status == "failed"
    assert "списку блокувань" in result.message
    state.unban.assert_not_awaited()
    row = (await fixture.database.case_history(GUILD))[0]
    assert row["action"] == "unban" and row["status"] == "failed"


@pytest.mark.parametrize("code", [10004, 10013, 0])
async def test_other_ban_read_404_is_not_reported_as_missing_ban(integrated, code):
    fixture, state = integrated, integrated.state
    state.get_ban.side_effect = api_error(404, code)
    result = await invoke(fixture, "unban")
    assert result.code == "not_sent" and "списку блокувань" not in result.message
    assert "private-api-payload" not in result.message
    state.unban.assert_not_awaited()


@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_write_404_stays_pending_and_same_interaction_never_replays(integrated, action):
    fixture, state = integrated, integrated.state
    getattr(state, action).side_effect = api_error(404, 10026)
    first = await invoke(fixture, action)
    assert first.code == "pending"
    # A later missing ban cannot resolve or replay an already ambiguous mutation.
    state.get_ban.side_effect = api_error(404, 10026)
    repeated = await invoke(fixture, action)
    assert repeated.code == "duplicate" and repeated.status == "pending"
    getattr(state, action).assert_awaited_once()
    assert state.get_ban.await_count == (1 if action == "unban" else 0)
    row = (await fixture.database.case_history(GUILD))[0]
    assert row["status"] == "pending" and row["id"] == repeated.case_id == first.case_id


async def test_real_fresh_permission_loss_after_claim_blocks_ban(integrated):
    fixture, state = integrated, integrated.state
    first, second = deepcopy(state.fresh), deepcopy(state.fresh)
    second["roles"][1]["permissions"] = "0"
    state.get_guild.side_effect = [first, second]
    result = await invoke(fixture, "ban")
    assert result.code == "not_sent" and result.status == "failed"
    assert state.get_guild.await_count == 2
    state.ban.assert_not_awaited()
    assert (await fixture.database.case_history(GUILD))[0]["status"] == "failed"


async def test_actual_sdk_success_with_unconfirmed_sql_finalization_is_not_failure(integrated, monkeypatch):
    fixture, state = integrated, integrated.state
    monkeypatch.setattr(fixture.database, "finish_case", AsyncMock(side_effect=OSError("private storage error")))
    result = await invoke(fixture, "ban")
    state.ban.assert_awaited_once()
    assert result.code == "applied_unrecorded" and result.status is None
    assert "Discord підтвердив ban" in result.message
    assert "private storage error" not in result.message
    assert (await fixture.database.case_history(GUILD))[0]["status"] == "pending"
