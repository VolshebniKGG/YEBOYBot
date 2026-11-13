"""Real SQLite → warning service → fresh discord.py REST models, offline."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core_v2.commands import Core
from core_v2.db import Database
from core_v2 import db as database_module
from core_v2.moderation import ModerationError, ModerationService
from test_ban_gateway import api_error, fail_member
from test_discord_moderation import (
    ACTOR, ACTOR_ROLE, BOT, GUILD, TARGET, TARGET_ROLE,
    member_payload, rest_snapshot as rest_snapshot,
)

REQUEST = 800
REASON = "Обґрунтоване попередження"


@pytest.fixture
async def integrated(rest_snapshot, tmp_path, monkeypatch):
    state = rest_snapshot
    bits = str(discord.Permissions(moderate_members=True).value)
    state.fresh["roles"][1]["permissions"] = bits
    state.fresh["roles"][2]["permissions"] = bits
    # Any mutation or DM is a bug: warn is only an audited local SQL record.
    state.writes = []
    for method in ("kick", "ban", "unban", "edit_member", "send_message", "start_private_message"):
        call = AsyncMock(side_effect=AssertionError("Warning attempted a Discord mutation"))
        monkeypatch.setattr(state.client.http, method, call)
        state.writes.append(call)
    database = Database("sqlite+aiosqlite:///" + str(tmp_path / "Попередження з пробілами.db"))
    try:
        await database.initialize()
        yield SimpleNamespace(database=database, state=state,
                              service=ModerationService(database, state.gateway))
    finally:
        for write in state.writes:
            write.assert_not_awaited()
        await database.close()


async def warn(fixture, request=REQUEST):
    return await fixture.service.warn(GUILD, ACTOR, TARGET, request, REASON)


async def test_fresh_sdk_permissions_override_stale_cache_and_warning_is_committed(integrated):
    fixture, state = integrated, integrated.state
    result = await warn(fixture)
    assert result.code == "applied" and result.status == "applied"
    rows = await fixture.database.warnings(GUILD, TARGET)
    assert len(rows) == 1
    assert rows[0]["id"] == f"{GUILD:016x}{REQUEST:016x}" == result.case_id
    assert rows[0]["reason"] == REASON and rows[0]["action"] == "warn"
    assert rows[0]["status"] == "applied" and rows[0]["created"] == rows[0]["updated"]
    assert not state.cached.get_role(ACTOR_ROLE).permissions.moderate_members
    assert len(state.get_guild.await_args_list) == 1
    assert {call.args[1] for call in state.get_member.await_args_list} == {ACTOR, BOT, TARGET}
    assert await fixture.database.warnings(GUILD + 1, TARGET) == []


async def test_concurrent_sdk_authorized_repeats_add_only_one_warning(integrated):
    fixture = integrated
    outcomes = await asyncio.gather(*(warn(fixture) for _ in range(8)))
    assert sum(result.code == "applied" for result in outcomes) == 1
    assert sum(result.code == "duplicate" for result in outcomes) == 7
    assert len({result.case_id for result in outcomes}) == 1
    assert len(await fixture.database.warnings(GUILD, TARGET)) == 1


async def test_repeat_after_revoke_and_service_restart_does_not_restore_warning(integrated):
    fixture = integrated
    result = await warn(fixture)
    assert await fixture.database.revoke_warning(GUILD, result.case_id) == 1
    before = (await fixture.database.case_history(GUILD))[0]
    fixture.service = ModerationService(fixture.database, fixture.state.gateway)
    replay = await warn(fixture)
    assert replay.code == "duplicate" and replay.status == "revoked"
    assert await fixture.database.warnings(GUILD, TARGET) == []
    assert (await fixture.database.case_history(GUILD))[0] == before


async def test_new_manual_interaction_intentionally_creates_a_separate_warning(integrated):
    first = await warn(integrated)
    second = await warn(integrated, request=REQUEST + 1)
    assert first.code == second.code == "applied" and first.case_id != second.case_id
    assert len(await integrated.database.warnings(GUILD, TARGET)) == 2


async def test_active_warning_limit_has_a_stable_id_tie_break(integrated, monkeypatch):
    monkeypatch.setattr(database_module.time, "time", lambda: 1_800_000_000.0)
    for request in range(REQUEST, REQUEST + 12):
        await warn(integrated, request=request)
    rows = await integrated.database.warnings(GUILD, TARGET)
    assert [row["id"] for row in rows] == [
        f"{GUILD:016x}{request:016x}" for request in range(REQUEST + 11, REQUEST + 1, -1)]
    assert len(await integrated.database.case_history(GUILD, page_size=10)) == 10


@pytest.mark.parametrize("outcome", ["error", "cancel"])
async def test_lost_database_ack_after_real_commit_is_not_falsely_reported_failed(integrated, monkeypatch, caplog, outcome):
    fixture = integrated
    original = fixture.database.record_warning

    async def lost(*arguments):
        await original(*arguments)
        if outcome == "cancel":
            raise asyncio.CancelledError
        raise OSError("private-database-ack-payload")

    monkeypatch.setattr(fixture.database, "record_warning", lost)
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await warn(fixture)
    else:
        result = await warn(fixture)
        assert result.code == "unconfirmed" and result.status is None
        assert "не підтверджено" in result.message and "не додано" not in result.message
    assert "private-database-ack-payload" not in caplog.text
    rows = await fixture.database.warnings(GUILD, TARGET)
    assert len(rows) == 1 and rows[0]["status"] == "applied"
    monkeypatch.setattr(fixture.database, "record_warning", original)
    fixture.service = ModerationService(fixture.database, fixture.state.gateway)
    assert (await warn(fixture)).code == "duplicate"
    assert await fixture.database.warnings(GUILD, TARGET) == rows


@pytest.mark.parametrize("changed", ["actor-permission", "bot-permission", "actor-timeout",
                                    "hierarchy", "owner", "unknown-role", "wrong-guild"])
async def test_fresh_sdk_denial_never_writes_a_warning(integrated, changed):
    fixture, state = integrated, integrated.state
    state.cached.get_role(ACTOR_ROLE)._permissions = discord.Permissions(administrator=True).value
    if changed == "actor-permission":
        state.fresh["roles"][1]["permissions"] = "0"
    elif changed == "bot-permission":
        state.fresh["roles"][2]["permissions"] = "0"
    elif changed == "actor-timeout":
        state.members[ACTOR] = member_payload(ACTOR, [ACTOR_ROLE], timed_out=True)
    elif changed == "hierarchy":
        next(role for role in state.fresh["roles"] if role["id"] == str(TARGET_ROLE))["position"] = 30
    elif changed == "owner":
        state.fresh["owner_id"] = str(TARGET)
    elif changed == "unknown-role":
        state.members[TARGET]["roles"].append("999999")
    else:
        state.fresh["id"] = str(GUILD + 1)
    with pytest.raises(ModerationError):
        await warn(fixture)
    assert await fixture.database.case_history(GUILD) == []


async def test_target_leaving_after_first_warning_blocks_repeat_but_preserves_receipt(integrated):
    fixture = integrated
    result = await warn(fixture)
    before = await fixture.database.case_history(GUILD)
    fail_member(fixture.state, TARGET, api_error(404, 10007))
    with pytest.raises(ModerationError):
        await warn(fixture)
    assert await fixture.database.case_history(GUILD) == before
    assert before[0]["id"] == result.case_id


async def test_command_reply_failure_does_not_undo_or_reapply_saved_warning(integrated):
    fixture = integrated
    actor = MagicMock(spec=discord.Member)
    actor.id = ACTOR
    interaction = SimpleNamespace(guild=SimpleNamespace(id=GUILD), user=actor, id=REQUEST,
        response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
    cog = Core(SimpleNamespace(db=fixture.database))
    cog.moderation = fixture.service
    interaction.followup.send.side_effect = OSError("offline lost followup")
    with pytest.raises(OSError):
        await Core.warn.callback(cog, interaction, SimpleNamespace(id=TARGET), REASON)
    before = await fixture.database.warnings(GUILD, TARGET)
    assert len(before) == 1
    interaction.followup.send.side_effect = None
    await Core.warn.callback(cog, interaction, SimpleNamespace(id=TARGET), REASON)
    assert await fixture.database.warnings(GUILD, TARGET) == before
    sent = interaction.followup.send.await_args
    assert sent.kwargs["ephemeral"] is True and sent.kwargs["allowed_mentions"].to_dict()["parse"] == []
    assert "Нове попередження не додано" in sent.args[0]
