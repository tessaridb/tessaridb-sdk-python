"""The vault frame's body (protocol §3.14) and the status every act answers with.

The passphrase is a field of the frame and never statement text. No type here
holds one, so no ``repr`` of anything here can print one.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ._bytes import Writer
from .errors import Malformed
from .value import Bool, Datetime, Duration, NoneValue, Object, Text, Value

__all__ = ["Custody", "SealState", "VaultStatus", "frame_body"]

_ACTS = {"status": 1, "unseal": 2, "seal": 3, "change": 4}


def frame_body(
    credentials: tuple[str, str] | None,
    act: str,
    *,
    passphrase: str | None = None,
    current: str | None = None,
    new: str | None = None,
    place: tuple[str, str, str] | None = None,
) -> bytes:
    """Credentials, the target (the store, or one vault by namespace, database
    and name), then the act and its fields."""
    w = Writer()
    if credentials is None:
        w.u8(0)
    else:
        w.u8(1)
        w.text(credentials[0])
        w.text(credentials[1])
    if place is None:
        w.u8(0)
    else:
        w.u8(1)
        for name in place:
            w.text(name)
    w.u8(_ACTS[act])
    if act == "unseal":
        w.text(passphrase or "")
    elif act == "change":
        w.text(current or "")
        w.text(new or "")
    return w.bytes()


class SealState(enum.Enum):
    """Whether the node can open secrets right now."""

    #: The store has no passphrase yet; the first unseal sets it.
    UNINITIALISED = "uninitialised"
    #: No key is held; nothing can be revealed.
    SEALED = "sealed"
    #: A key is held until :attr:`VaultStatus.seals_at`.
    UNSEALED = "unsealed"


class Custody(enum.Enum):
    """What opens a vault."""

    #: Its own passphrase; the store's opens nothing in it.
    OWN = "own"
    #: The store's passphrase. The state reported beside it is the store's.
    STORE = "store"


@dataclass(frozen=True)
class VaultStatus:
    """The node's answer to every vault act.

    ``seals_at`` is to the microsecond, which is all ``datetime`` holds: it says
    when a window closes, and nothing depends on the last three digits.
    """

    state: SealState
    seals_at: datetime | None
    unseal_for: timedelta
    #: Whether this unseal set the store's first passphrase — the moment a
    #: mistyped passphrase became the passphrase.
    initialised: bool
    #: For an act on one vault, what opens it; ``None`` for the store's own.
    custody: Custody | None

    @classmethod
    def read(cls, value: Value) -> VaultStatus:
        if not isinstance(value, Object):
            raise Malformed("a vault status is an object")
        fields = value.fields
        state = fields.get("state")
        unseal_for = fields.get("unseal_for")
        if not isinstance(state, Text) or not isinstance(unseal_for, Duration):
            raise Malformed("a vault status carries a state and a period")
        seals_at = fields.get("seals_at")
        if isinstance(seals_at, Datetime):
            at: datetime | None = datetime.fromtimestamp(
                seals_at.seconds, timezone.utc
            ) + timedelta(microseconds=seals_at.nanos // 1000)
        elif seals_at is None or isinstance(seals_at, NoneValue):
            at = None
        else:
            raise Malformed("seals_at is a datetime")
        custody = fields.get("custody")
        if custody is not None and not isinstance(custody, Text):
            raise Malformed("custody is a string")
        try:
            return cls(
                state=SealState(state.value),
                seals_at=at,
                unseal_for=timedelta(
                    seconds=unseal_for.seconds, microseconds=unseal_for.nanos // 1000
                ),
                initialised=fields.get("initialised") == Bool(True),
                custody=Custody(custody.value) if isinstance(custody, Text) else None,
            )
        except ValueError as why:
            raise Malformed(f"a vault status outside its closed sets: {why}") from why
