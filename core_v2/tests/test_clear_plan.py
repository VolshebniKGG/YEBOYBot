"""Versioned clear-request payload boundaries; no Discord or package downloads."""
from dataclasses import FrozenInstanceError, replace
import json

import pytest

from core_v2.clear_plan import CLEAR_STATES, ClearPlan, request_key, valid_key, valid_reason


def plan(**changes):
    values = dict(id=request_key(10, 1000), guild_id=10, actor_id=20, channel_id=30,
                  request_id=1000, reason="Обґрунтована причина", requested=5,
                  message_ids=(999, 998), expires=1600.0,
                  skipped_pinned=1, skipped_old=1, skipped_system=1)
    values.update(changes)
    return ClearPlan(**values)


def row(value, **changes):
    result = dict(id=value.id, guild_id=value.guild_id, user_id=value.actor_id,
                  channel_id=value.channel_id, body=value.encode(), due=value.expires,
                  lease=0, attempts=0, state="clear_confirm")
    result.update(changes)
    return result


@pytest.mark.parametrize("state", sorted(CLEAR_STATES))
def test_plan_round_trip_preserves_confirmation_and_audit_fields_for_every_terminal_state(state):
    original = plan(reason="  Причина 🚀 «в лапках» \\\u200d  ")
    recovered = ClearPlan.decode(row(original, state=state))
    assert recovered == original
    assert recovered.reason == "Причина 🚀 «в лапках» \\\u200d"
    assert recovered.message_ids == (999, 998)
    with pytest.raises(FrozenInstanceError):
        recovered.channel_id = 40


@pytest.mark.parametrize("reason", ["\\" * 200, '"' * 200, "🚀" * 200,
                                   "Ї" * 200], ids=["backslashes", "quotes", "emoji", "ukrainian"])
def test_full_reason_and_25_maximum_snowflakes_fit_existing_body_column_without_truncation(reason):
    maximum = 2**63 - 1
    original = plan(id=request_key(maximum, maximum), guild_id=maximum,
                    actor_id=maximum, channel_id=maximum, request_id=maximum,
                    reason=reason, requested=25,
                    message_ids=tuple(maximum - offset for offset in range(1, 26)),
                    skipped_pinned=0, skipped_old=0, skipped_system=0)
    encoded = original.encode()
    assert len(encoded) <= 1000
    assert len(json.loads(encoded)[2]) == 25
    assert ClearPlan.decode(row(original)).reason == reason
    assert ClearPlan.decode(row(original)).message_ids == original.message_ids


@pytest.mark.parametrize("changes", [
    {"guild_id": 0}, {"guild_id": True}, {"actor_id": -1}, {"actor_id": 2**63},
    {"channel_id": "30"}, {"request_id": 1.0},
    {"id": request_key(11, 1000)}, {"id": request_key(10, 1001)},
    {"requested": 0}, {"requested": 26}, {"requested": True},
    {"message_ids": ()}, {"message_ids": [999]}, {"message_ids": (999, 999)},
    {"message_ids": (1000,)}, {"message_ids": (1001,)}, {"message_ids": (0,)},
    {"message_ids": (True,)}, {"message_ids": ("999",)},
    {"message_ids": tuple(range(1, 7))},
    {"expires": 0}, {"expires": -1}, {"expires": float("nan")},
    {"expires": float("inf")}, {"expires": True}, {"expires": "1600"},
    {"skipped_pinned": -1}, {"skipped_old": True}, {"skipped_system": 6},
    {"skipped_pinned": 4},
], ids=["zero-guild", "bool-guild", "negative-actor", "oversize-actor", "text-channel",
        "float-interaction", "foreign-guild-key", "different-interaction-key",
        "zero-limit", "oversize-limit", "bool-limit", "empty-selection", "mutable-selection",
        "duplicate-selection", "same-command-id", "newer-message", "zero-message", "bool-message",
        "text-message", "too-many-messages", "zero-expiry", "past-zero-expiry", "nan-expiry",
        "infinite-expiry", "bool-expiry", "text-expiry", "negative-skipped", "bool-skipped",
        "oversize-skipped", "combined-over-limit"])
def test_invalid_plan_cannot_be_used_as_a_confirmation(changes):
    with pytest.raises(ValueError):
        plan(**changes)


@pytest.mark.parametrize("reason", [None, 10, "", "   ", "x" * 201,
                                   "valid\0invalid", "valid\ninvalid", "valid\tinvalid",
                                   "valid\x7finvalid", "valid\x85invalid", "valid\x9finvalid",
                                   "valid\ud800invalid"],
                         ids=["none", "numeric", "empty", "spaces", "overlong", "nul",
                              "newline", "tab", "delete-control", "c1-newline", "c1-control", "surrogate"])
def test_invalid_reason_is_refused_without_silent_loss(reason):
    with pytest.raises(ValueError):
        valid_reason(reason)
    with pytest.raises(ValueError):
        plan(reason=reason)


@pytest.mark.parametrize("identifier", [None, 10, "A" * 32, "g" * 32, "0" * 31,
                                        "0" * 33, "0" * 31 + "\n"],
                         ids=["none", "numeric", "uppercase", "not-hex", "short", "long", "newline"])
def test_request_key_uses_only_exact_lowercase_32_hex_characters(identifier):
    with pytest.raises(ValueError):
        valid_key(identifier)


@pytest.mark.parametrize("body", [
    "{broken", "[]", "null", "{}", "[true,\"r\",[999],1,[0,0,0]]",
    "[2,\"r\",[999],1,[0,0,0]]", "[1.0,\"r\",[999],1,[0,0,0]]",
    "[1,\"r\",999,1,[0,0,0]]", "[1,\"r\",[999],1,0]",
    "[1,\"r\",[999],1,[0,0]]", "[1,\"r\",[],1,[0,0,0]]",
    "x" * 1001, None, [],
], ids=["invalid-json", "empty", "null", "object", "bool-version", "future-version",
        "float-version", "not-message-list", "not-skipped-list", "short-counts",
        "empty-selection", "oversize-body", "null-body", "list-body"])
def test_unknown_or_corrupted_payload_never_becomes_an_executable_plan(body):
    with pytest.raises(ValueError):
        ClearPlan.decode(row(plan(), body=body))


@pytest.mark.parametrize("state", ["pending", "done", "failed", "cancelled", "unknown", None])
def test_reminder_and_unknown_states_cannot_be_decoded_as_clear_requests(state):
    with pytest.raises(ValueError):
        ClearPlan.decode(row(plan(), state=state))


def test_storage_scope_changes_and_corrupted_snowflake_keys_are_not_accepted():
    stored = row(plan())
    with pytest.raises(ValueError):
        ClearPlan.decode(dict(stored, guild_id=11))
    with pytest.raises(ValueError):
        ClearPlan.decode(dict(stored, id="0" * 32))
    # The plan deliberately retains typed column scope rather than redundant JSON.
    assert ClearPlan.decode(dict(stored, user_id=21)).actor_id == 21
    assert ClearPlan.decode(dict(stored, channel_id=31)).channel_id == 31


def test_different_ids_and_expiry_produce_different_persisted_plans():
    first = plan()
    assert replace(first, expires=first.expires + 1) != first
    assert replace(first, message_ids=(997, 996)).encode() != first.encode()
    assert request_key(10, 1000) != request_key(11, 1000)
