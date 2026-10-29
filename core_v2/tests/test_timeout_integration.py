"""Durable timeout requests through SQLite and the real discord.py edit adapter."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core_v2.commands import Core
from core_v2.db import Database
from core_v2.moderation import ModerationError, ModerationResult, ModerationService
from test_discord_moderation import (
    ACTOR, GUILD, TARGET, rest_snapshot as rest_snapshot,
)


@pytest.fixture
async def integrated(rest_snapshot, tmp_path, monkeypatch):
    state = rest_snapshot
    for role in state.fresh['roles'][1:3]:
        role['permissions'] = str(discord.Permissions(moderate_members=True).value)
    state.edit = AsyncMock(return_value=state.members[TARGET])
    monkeypatch.setattr(state.client.http, 'edit_member', state.edit)
    db = Database('sqlite+aiosqlite:///' + str(tmp_path / 'timeout.db'))
    await db.initialize()
    try:
        yield SimpleNamespace(state=state, db=db, service=ModerationService(db, state.gateway))
    finally:
        await db.close()


async def invoke(fixture, action, minutes=5):
    args = (GUILD, ACTOR, TARGET, 800)
    return await getattr(fixture.service, action)(*args, *([minutes, 'Причина'] if action == 'timeout' else []))


@pytest.mark.parametrize('action', ['timeout', 'untimeout'])
async def test_committed_claim_before_sdk_edit_and_duplicate_never_replays(integrated, action):
    fixture = integrated
    before = discord.utils.utcnow()

    async def edit(guild_id, user_id, **kwargs):
        assert (guild_id, user_id) == (GUILD, TARGET)
        rows = await fixture.db.case_history(GUILD)
        assert len(rows) == 1 and rows[0]['status'] == 'pending'
        assert rows[0]['id'] in kwargs['reason']
        value = kwargs['communication_disabled_until']
        if action == 'timeout':
            assert 299 <= (datetime.fromisoformat(value) - before).total_seconds() <= 305
        else:
            assert value is None
        return fixture.state.members[TARGET]

    fixture.state.edit.side_effect = edit
    first = await invoke(fixture, action)
    assert first.code == 'applied'
    repeated = await invoke(fixture, action)
    assert repeated.code == 'duplicate' and repeated.status == 'applied'
    fixture.state.edit.assert_awaited_once()
    rows = await fixture.db.case_history(GUILD)
    assert rows[0]['action'] == action and rows[0]['status'] == 'applied'
    assert await fixture.db.case_history(GUILD + 1) == []


@pytest.mark.parametrize('action', ['timeout', 'untimeout'])
async def test_fresh_permission_loss_after_claim_blocks_edit(integrated, action):
    first, second = deepcopy(integrated.state.fresh), deepcopy(integrated.state.fresh)
    second['roles'][1]['permissions'] = '0'
    integrated.state.get_guild.side_effect = [first, second]
    result = await invoke(integrated, action)
    assert result.code == 'not_sent'
    integrated.state.edit.assert_not_awaited()
    assert (await integrated.db.case_history(GUILD))[0]['status'] == 'failed'


@pytest.mark.parametrize('action', ['timeout', 'untimeout'])
async def test_higher_target_role_blocks_edit(integrated, action):
    integrated.state.fresh['roles'][3]['position'] = 100
    assert (await invoke(integrated, action)).code == 'not_sent'
    integrated.state.edit.assert_not_awaited()


async def test_admin_target_cannot_receive_timeout(integrated):
    integrated.state.fresh['roles'][3]['permissions'] = str(discord.Permissions(administrator=True).value)
    result = await invoke(integrated, 'timeout')
    assert result.code == 'not_sent' and 'адміністратора' in result.message
    integrated.state.edit.assert_not_awaited()


@pytest.mark.parametrize('action', ['timeout', 'untimeout'])
async def test_ambiguous_edit_stays_pending_and_cannot_be_replayed(integrated, action):
    integrated.state.edit.side_effect = TimeoutError('private-network-details')
    result = await invoke(integrated, action)
    assert result.code == 'pending' and 'private-network' not in result.message
    repeated = await invoke(integrated, action)
    assert repeated.code == 'duplicate' and repeated.status == 'pending'
    integrated.state.edit.assert_awaited_once()


async def test_changed_duration_does_not_overwrite_or_reapply_same_request(integrated):
    assert (await invoke(integrated, 'timeout', 5)).code == 'applied'
    assert (await invoke(integrated, 'timeout', 10)).code == 'not_sent_unrecorded'
    integrated.state.edit.assert_awaited_once()
    assert (await integrated.db.case_history(GUILD))[0]['reason'] == '[timeout=5m] Причина'


@pytest.mark.parametrize('minutes', [0, 40321, True, 1.5])
async def test_invalid_duration_never_reaches_discord_or_db(integrated, minutes):
    with pytest.raises(ModerationError):
        await invoke(integrated, 'timeout', minutes)
    integrated.state.get_guild.assert_not_awaited()
    assert await integrated.db.case_history(GUILD) == []


@pytest.mark.parametrize('action', ['timeout', 'untimeout'])
async def test_commands_defer_and_bind_interaction_id_before_service(action):
    from unittest.mock import MagicMock
    actor = MagicMock(spec=discord.Member)
    actor.id = ACTOR
    interaction = SimpleNamespace(guild=SimpleNamespace(id=GUILD), user=actor, id=800,
        response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
    cog = Core(SimpleNamespace(db=object()))

    async def call(*args):
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        assert args == (GUILD, ACTOR, TARGET, 800, *([5, 'Причина'] if action == 'timeout' else []))
        return ModerationResult('a' * 32, 'applied', 'applied', 'Збережено')

    cog.moderation = SimpleNamespace(**{action: AsyncMock(side_effect=call)})
    await getattr(Core, action).callback(cog, interaction, SimpleNamespace(id=TARGET),
                                       *([5, 'Причина'] if action == 'timeout' else []))
    sent = interaction.followup.send.await_args
    assert sent.kwargs['ephemeral'] is True
    assert sent.kwargs['allowed_mentions'].to_dict()['parse'] == []
