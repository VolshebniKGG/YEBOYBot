"""Bounded, versioned clear plans stored in existing manual-job rows."""
from dataclasses import dataclass
import json
import math
import re

from .moderation import valid_id

MAX_MESSAGES = 25
CONFIRM_SECONDS = 60
MAX_AGE_SECONDS = 7 * 86400
MAX_ACTIVE_CONFIRMATIONS = 5
CLEAR_STATES = frozenset({"clear_confirm", "clear_running", "clear_done", "clear_pending",
                          "clear_cancelled", "clear_expired", "clear_failed"})


class ClearLimitError(ValueError):
    """Expected capacity refusal, distinct from untrusted database errors."""


def request_key(guild_id, interaction_id):
    return f"{valid_id(guild_id):016x}{valid_id(interaction_id):016x}"


def valid_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Invalid clear request ID")
    return value


def valid_reason(value):
    if (not isinstance(value, str) or not value.strip() or len(value.strip()) > 200 or
            any(ord(character) < 32 or 127 <= ord(character) <= 159 or
                0xD800 <= ord(character) <= 0xDFFF for character in value)):
        raise ValueError("Clear reason must contain 1–200 printable characters")
    return value.strip()


@dataclass(frozen=True)
class ClearPlan:
    id: str
    guild_id: int
    actor_id: int
    channel_id: int
    request_id: int
    reason: str
    requested: int
    message_ids: tuple[int, ...]
    expires: float
    skipped_pinned: int = 0
    skipped_old: int = 0
    skipped_system: int = 0

    def __post_init__(self):
        for value in (self.guild_id, self.actor_id, self.channel_id, self.request_id):
            valid_id(value)
        if valid_key(self.id) != request_key(self.guild_id, self.request_id):
            raise ValueError("Clear request does not match its guild/interaction")
        object.__setattr__(self, "reason", valid_reason(self.reason))
        if type(self.requested) is not int or not 1 <= self.requested <= MAX_MESSAGES:
            raise ValueError("Invalid clear limit")
        if not isinstance(self.message_ids, tuple) or not 1 <= len(self.message_ids) <= self.requested:
            raise ValueError("Invalid clear messages")
        for identifier in self.message_ids:
            valid_id(identifier)
            if identifier >= self.request_id:
                raise ValueError("Clear may only select messages preceding the command")
        if len(set(self.message_ids)) != len(self.message_ids):
            raise ValueError("Duplicate clear message")
        if type(self.expires) not in (int, float) or not math.isfinite(self.expires) or self.expires <= 0:
            raise ValueError("Invalid clear expiry")
        skipped = (self.skipped_pinned, self.skipped_old, self.skipped_system)
        if any(type(value) is not int or not 0 <= value <= self.requested for value in skipped):
            raise ValueError("Invalid clear skipped counts")
        if len(self.message_ids) + sum(skipped) > self.requested:
            raise ValueError("Clear selection exceeds its requested bound")
        if len(self.encode()) > 1000:
            raise ValueError("Clear plan exceeds the existing storage bound")

    def encode(self):
        # Identity/scope/expiry are existing typed columns, not duplicated JSON.
        # Printable 200-char reasons + at most 25 signed BIGINTs fit body(1000).
        return json.dumps([1, self.reason, list(self.message_ids), self.requested,
                           [self.skipped_pinned, self.skipped_old, self.skipped_system]],
                          ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def decode(cls, row):
        if (row["state"] not in CLEAR_STATES or not isinstance(row["body"], str) or
                len(row["body"]) > 1000):
            raise ValueError("Not a supported clear request")
        data = json.loads(row["body"])
        if (not isinstance(data, list) or len(data) != 5 or type(data[0]) is not int or data[0] != 1 or
                not isinstance(data[2], list) or not isinstance(data[4], list) or len(data[4]) != 3):
            raise ValueError("Unsupported clear payload")
        identifier = valid_key(row["id"])
        return cls(identifier, row["guild_id"], row["user_id"], row["channel_id"],
                   int(identifier[16:], 16), data[1], data[3], tuple(data[2]), row["due"], *data[4])
