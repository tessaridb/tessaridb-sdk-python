"""Consuming a topic as a member of a consumer group — consumer contract 1.0
(``spec/consumer-v1.md`` in the protocol repository).

A :class:`Consumer` reads a topic under a group the store holds, and calls a
handler once per message in the order the group hands them out. The group, not
the connection, keeps the state — the last position handed out and what is in
flight — so a process that crashes loses nothing it had not acknowledged, and
another under the same group name carries on.

- :meth:`Consumer.run_auto` acknowledges each message when the handler returns,
  and hands it back when the handler raises: acknowledge after processing, at
  least once.
- :meth:`Consumer.run_manual` lets the handler decide by returning
  :class:`Ack`, :class:`Nack` or :class:`Leave`.

The group itself is declared in the store (``DEFINE GROUP``), never by this
class: declaring it is a schema act that chooses a deadline no client can guess.

The loop is synchronous, as every call in this client is; run it in a thread of
its own and call :meth:`Consumer.stop` from another.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Callable

from .connection import Connection
from .outcome import Records, ValueOutcome
from .query import BuilderError
from .value import Integer, NoneValue, Object, Value

__all__ = ["Consumer", "Message", "Ack", "Nack", "Leave", "Settle"]

#: The first wait after a read that answered nothing, in seconds (§4.5).
FIRST_WAIT = 0.05
#: The longest wait between reads that answer nothing, in seconds (§4.5).
LONGEST_WAIT = 1.0

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_GROUP = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


@dataclass(frozen=True)
class Message:
    """One message, as the group handed it out."""

    #: Its position in the topic, from 1 — with the topic and group names, a
    #: stable key for making an outside effect idempotent.
    position: int
    value: Value
    #: How many times it has been handed out, 1 the first time.
    deliveries: int


@dataclass(frozen=True)
class Ack:
    """Done: never handed out to this group again."""


@dataclass(frozen=True)
class Nack:
    """Hand it out again — now, or after ``delay`` seconds."""

    delay: float | None = None


@dataclass(frozen=True)
class Leave:
    """Neither: the group hands it out again when its deadline passes."""


Settle = Ack | Nack | Leave


def _whole(value: Value | None) -> int:
    if isinstance(value, Integer) and value.value >= 0:
        return value.value
    raise ValueError(f"expected a whole number, got {value!r}")


class Consumer:
    """A member of ``group`` reading ``topic`` in ``namespace``/``database``.

    The connection should already carry its credentials when the store is
    closed: a wire connection proves who it is once and keeps that identity.
    Names are checked before anything is sent (§3) and refused with
    :class:`~tessaridb.BuilderError` rather than escaped.
    """

    def __init__(
        self,
        connection: Connection,
        namespace: str,
        database: str,
        topic: str,
        group: str,
        batch: int = 10,
    ) -> None:
        for what, name in (("a namespace", namespace), ("a database", database), ("a topic", topic)):
            if not _NAME.match(name):
                raise BuilderError("not-a-name", what, name)
        if not _GROUP.match(group):
            raise BuilderError("not-a-name", "a group", group)
        self._connection = connection
        # Sent with every statement: a connection that reconnected has forgotten
        # any earlier USE (§5).
        self._tenancy = f"USE NAMESPACE {namespace}; USE DATABASE {database}; "
        self._topic = topic
        self._group = group
        self._batch = max(1, batch)
        self._stopped = threading.Event()

    def stop(self) -> None:
        """Let the running handler finish (and, in auto mode, its acknowledgement
        be sent), then stop reading. What is in flight returns to the group when
        its deadline passes."""
        self._stopped.set()

    def run_auto(self, handler: Callable[[Message], object]) -> None:
        """Call ``handler`` for each message. Returning acknowledges it; raising
        hands it back at once and carries on with the next."""
        while (messages := self._next_batch()) is not None:
            for message in messages:
                try:
                    handler(message)
                except Exception:  # noqa: BLE001 — any failure hands the message back (§4.3)
                    self.nack(message.position)
                else:
                    self.ack(message.position)
                if self._stopped.is_set():
                    return

    def run_manual(self, handler: Callable[[Message], Settle]) -> None:
        """Call ``handler`` for each message and do what it returns."""
        while (messages := self._next_batch()) is not None:
            for message in messages:
                decided = handler(message)
                if isinstance(decided, Ack):
                    self.ack(message.position)
                elif isinstance(decided, Nack):
                    self.nack(message.position, delay=decided.delay)
                if self._stopped.is_set():
                    return

    def ack(self, *positions: int) -> int:
        """Acknowledge these positions; answers how many were in flight. One that
        was not counts nothing and is not an error."""
        return self._settle(f"ACK {self._topic} FOR CONSUMER '{self._group}' AT ", positions, "")

    def nack(self, *positions: int, delay: float | None = None) -> int:
        """Hand these positions back, now or after ``delay`` seconds; answers how
        many were in flight."""
        # A delay is a duration literal in the grammar, not a parameter, written
        # from a number formatted here and never from a caller's text.
        millis = int(delay * 1000) if delay is not None else 0
        tail = f" DELAY {millis}ms" if millis > 0 else ""
        return self._settle(f"NACK {self._topic} FOR CONSUMER '{self._group}' AT ", positions, tail)

    def _settle(self, statement: str, positions: tuple[int, ...], tail: str) -> int:
        if not positions:
            return 0
        names = [f"p{index}" for index in range(len(positions))]
        script = self._tenancy + statement + ", ".join(f"${name}" for name in names) + tail + ";"
        reply = self._connection.execute(
            script, {name: Integer(position) for name, position in zip(names, positions)}
        )
        answered = reply.outcomes[-1] if reply.outcomes else None
        if not isinstance(answered, ValueOutcome):
            raise ValueError(f"an acknowledgement answered {answered!r}")
        return _whole(answered.value)

    def _next_batch(self) -> list[Message] | None:
        wait = FIRST_WAIT
        while not self._stopped.is_set():
            reply = self._connection.execute(
                f"{self._tenancy}READ FROM {self._topic} FOR CONSUMER '{self._group}' LIMIT {self._batch};"
            )
            answered = reply.outcomes[-1] if reply.outcomes else None
            if not isinstance(answered, Records):
                raise ValueError(f"a group read answered {answered!r}")
            messages = [_message(row.value) for row in answered.rows]
            if messages:
                return messages
            # Woken early by stop(), so a stop during the wait is not held back.
            self._stopped.wait(wait)
            wait = min(wait * 2, LONGEST_WAIT)
        return None


def _message(body: Value) -> Message:
    if not isinstance(body, Object):
        raise ValueError(f"a message answered {body!r}")
    return Message(
        position=_whole(body.fields.get("position")),
        value=body.fields.get("value", NoneValue()),
        deliveries=_whole(body.fields.get("deliveries")),
    )
