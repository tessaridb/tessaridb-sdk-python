"""A space used as a cache, a counter and a lock — cache contract 1.0
(``spec/cache-v1.md`` in the protocol repository).

A :class:`Cache` uses a connection the caller holds and sends one statement per
call, with its own ``USE``, so a connection that reconnected underneath it cannot
read another database. Every key, value, duration and holder is bound.

Two things a cache over this store must know, and that this class makes hard to
get wrong:

- **A plain** :meth:`Cache.set` **clears an expiry the key had.** Pass the ttl
  again on every write that must keep one.
- **A lock is a lease, not a mutex.** Past its ttl another holder may take it and
  neither is told. :meth:`Lease.release` is an expiring conditional write, never a
  delete: a delete after the lease lapsed would remove the next holder's lock, and
  a hand-back with no expiry would make the key permanent.

A ttl is seconds (``int`` or ``float``) or a :class:`datetime.timedelta`.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Union

from .connection import Connection
from .outcome import Done, Keys, ValueOutcome
from .query import BuilderError
from .value import NONE, NULL, Bool, Duration, Integer, NoneValue, Text, Value

__all__ = ["Cache", "Lease", "NotACacheArgument", "TtlLike"]

TtlLike = Union[int, float, timedelta]

#: The most keys one listing may ask for (§2).
MOST_KEYS = 1000

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class NotACacheArgument(ValueError):
    """An argument the cache contract refuses before sending (§2) — a ttl that is
    not positive, which would remove a key, a listing out of range, an empty lock
    holder."""


def _duration(ttl: TtlLike) -> Duration:
    """The ttl as the store's duration, refused unless it is positive."""
    if isinstance(ttl, timedelta):
        micros = (ttl.days * 86_400 + ttl.seconds) * 1_000_000 + ttl.microseconds
        nanos_total = micros * 1_000
    elif isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
        raise NotACacheArgument("a ttl is seconds or a timedelta")
    else:
        nanos_total = round(ttl * 1_000_000_000)
    if nanos_total <= 0:
        raise NotACacheArgument(
            "a ttl must be positive: a zero or negative one would remove the key"
        )
    return Duration(nanos_total // 1_000_000_000, nanos_total % 1_000_000_000)


def unquoted(spelled: str) -> str:
    """A key as the wire spells it, back into the string this handle wrote (§2):
    a quoted text key loses its quotes and its two escapes; any other kind is
    returned as it came."""
    if len(spelled) < 2 or not (spelled.startswith("'") and spelled.endswith("'")):
        return spelled
    out: list[str] = []
    escaped = False
    for character in spelled[1:-1]:
        if escaped:
            out.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        else:
            out.append(character)
    return "".join(out)


class _Statements:
    """The statements a cache sends (§2), rendered in one place."""

    def __init__(self, namespace: str, database: str, space: str) -> None:
        for what, name in (
            ("a namespace", namespace),
            ("a database", database),
            ("a space", space),
        ):
            if not _NAME.match(name):
                raise BuilderError("not-a-name", what, name)
        # Sent with every statement: a connection that reconnected has forgotten
        # any earlier USE (§1).
        self._tenancy = f"USE NAMESPACE {namespace}; USE DATABASE {database}; "
        self._space = space

    def _keyed(self, statement: str, key: str) -> tuple[str, dict[str, Value]]:
        return f"{self._tenancy}{statement}", {"k": Text(key)}

    def get(self, key: str) -> tuple[str, dict[str, Value]]:
        return self._keyed(f"GET {self._space}:$k;", key)

    def set(
        self,
        key: str,
        value: Value,
        condition: str = "",
        expected: Value | None = None,
        ttl: Duration | None = None,
    ) -> tuple[str, dict[str, Value]]:
        expiry = " EXPIRE $t" if ttl is not None else ""
        script, given = self._keyed(
            f"SET {self._space}:$k = $v{condition}{expiry};", key
        )
        given["v"] = value
        if expected is not None:
            given["e"] = expected
        if ttl is not None:
            given["t"] = ttl
        return script, given

    def delete(self, key: str) -> tuple[str, dict[str, Value]]:
        return self._keyed(f"DELETE {self._space}:$k RETURN BEFORE;", key)

    def incr(self, key: str, by: int) -> tuple[str, dict[str, Value]]:
        script, given = self._keyed(f"INCR {self._space}:$k BY $n;", key)
        given["n"] = Integer(by)
        return script, given

    def ttl(self, key: str) -> tuple[str, dict[str, Value]]:
        return self._keyed(f"RETURN TTL {self._space}:$k;", key)

    def expire(self, key: str, ttl: Duration) -> tuple[str, dict[str, Value]]:
        script, given = self._keyed(f"EXPIRE {self._space}:$k $t;", key)
        given["t"] = ttl
        return script, given

    def persist(self, key: str) -> tuple[str, dict[str, Value]]:
        return self._keyed(f"PERSIST {self._space}:$k;", key)

    def keys(
        self, prefix: str | None, after: str | None, limit: int
    ) -> tuple[str, dict[str, Value]]:
        script = f"{self._tenancy}KEYS FROM {self._space}"
        given: dict[str, Value] = {}
        if prefix:
            script += " PREFIX $p"
            given["p"] = Text(prefix)
        if after is not None:
            script += " AFTER $a"
            given["a"] = Text(after)
        # The one number written into the text: an int this handle checked.
        return f"{script} LIMIT {int(limit)};", given

    def lock(
        self, key: str, holder: str, ttl: Duration
    ) -> tuple[str, dict[str, Value]]:
        return self._held(
            f"SET {self._space}:$k = $h IF ABSENT EXPIRE $t;", key, holder, ttl
        )

    def extend(
        self, key: str, holder: str, ttl: Duration
    ) -> tuple[str, dict[str, Value]]:
        return self._held(
            f"SET {self._space}:$k = $h IF = $h EXPIRE $t;", key, holder, ttl
        )

    def release(self, key: str, holder: str) -> tuple[str, dict[str, Value]]:
        # Never a delete and never a write without an expiry (§4).
        return self._held(
            f"SET {self._space}:$k = 'free' IF = $h EXPIRE 1ms;", key, holder, None
        )

    def _held(
        self, statement: str, key: str, holder: str, ttl: Duration | None
    ) -> tuple[str, dict[str, Value]]:
        script, given = self._keyed(statement, key)
        given["h"] = Text(holder)
        if ttl is not None:
            given["t"] = ttl
        return script, given


@dataclass
class Lease:
    """A lock held by this caller until its ttl passes (§4)."""

    key: str
    holder: str
    ttl: Duration
    _cache: Cache

    def extend(self, ttl: TtlLike | None = None) -> bool:
        """Hold it for another ttl (its own when none is given); ``False`` means the
        lease was already lost and the work it guarded must stop."""
        lasting = self.ttl if ttl is None else _duration(ttl)
        return self._cache._flag(
            *self._cache._statements.extend(self.key, self.holder, lasting)
        )

    def release(self) -> bool:
        """Give it back; whether it was still held."""
        return self._cache._flag(
            *self._cache._statements.release(self.key, self.holder)
        )


class Cache:
    """The space ``space`` in ``namespace``/``database``, over ``connection``.

    The connection should already carry its credentials when the store is closed.
    Names are checked before anything is sent and refused with
    :class:`~tessaridb.BuilderError` rather than escaped.
    """

    def __init__(
        self, connection: Connection, namespace: str, database: str, space: str
    ) -> None:
        self._statements = _Statements(namespace, database, space)
        self._connection = connection

    def get(self, key: str) -> Value | None:
        """The value under ``key``, or ``None`` when there is no such key."""
        found = self._value(*self._statements.get(key))
        return None if isinstance(found, NoneValue) else found

    def set(self, key: str, value: Value, ttl: TtlLike | None = None) -> None:
        """Store ``value``, expiring after ``ttl`` if one is given — and clearing
        any expiry the key had if not."""
        lasting = None if ttl is None else _duration(ttl)
        self._value(*self._statements.set(key, value, ttl=lasting))

    def set_if_absent(self, key: str, value: Value, ttl: TtlLike | None = None) -> bool:
        """Store ``value`` only if there is no ``key``; whether it was stored."""
        lasting = None if ttl is None else _duration(ttl)
        return self._flag(*self._statements.set(key, value, " IF ABSENT", ttl=lasting))

    def set_if_present(
        self, key: str, value: Value, ttl: TtlLike | None = None
    ) -> bool:
        """Store ``value`` only if there is a ``key``; whether it was stored."""
        lasting = None if ttl is None else _duration(ttl)
        return self._flag(*self._statements.set(key, value, " IF PRESENT", ttl=lasting))

    def compare_and_set(
        self, key: str, expected: Value, value: Value, ttl: TtlLike | None = None
    ) -> bool:
        """Store ``value`` only if ``key`` holds ``expected``; whether it was stored."""
        lasting = None if ttl is None else _duration(ttl)
        return self._flag(
            *self._statements.set(key, value, " IF = $e", expected, lasting)
        )

    def delete(self, key: str) -> bool:
        """Remove ``key``; whether there was one. A key holding ``NULL`` is one."""
        return not isinstance(self._value(*self._statements.delete(key)), NoneValue)

    def incr(self, key: str, by: int = 1) -> int:
        """Add ``by`` — a missing key counts from zero — and answer the new value.
        An expiry the key had is kept."""
        found = self._value(*self._statements.incr(key, by))
        if not isinstance(found, Integer):
            raise ValueError(f"an increment answered {found!r}")
        return found.value

    def ttl(self, key: str) -> Value:
        """How long ``key`` has left: a :class:`~tessaridb.Duration`, ``NULL`` when it
        never expires, or ``NONE`` when there is no key — the store's two absences,
        kept apart."""
        found = self._value(*self._statements.ttl(key))
        if not isinstance(found, (Duration, NoneValue)) and found != NULL:
            raise ValueError(f"a ttl answered {found!r}")
        return found

    def expire(self, key: str, ttl: TtlLike) -> bool:
        """Let ``key`` expire after ``ttl``; whether there was a key."""
        return self._flag(*self._statements.expire(key, _duration(ttl)))

    def persist(self, key: str) -> bool:
        """Make ``key`` never expire; whether there was a key."""
        return self._flag(*self._statements.persist(key))

    def keys(
        self, prefix: str | None = None, after: str | None = None, limit: int = 100
    ) -> list[str]:
        """Up to ``limit`` keys (1–1000) in key order, starting with ``prefix`` (none
        or empty: every key) and after ``after``."""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MOST_KEYS
        ):
            raise NotACacheArgument("a key listing asks for 1 to 1000 keys")
        reply = self._connection.execute(*self._statements.keys(prefix, after, limit))
        answered = reply.outcomes[-1] if reply.outcomes else None
        if not isinstance(answered, Keys):
            raise ValueError(f"a key listing answered {answered!r}")
        return [unquoted(key) for key in answered.keys]

    def get_or_set(self, key: str, ttl: TtlLike, loader: Callable[[], Value]) -> Value:
        """The value under ``key``, or — when there is none — what ``loader`` makes,
        stored for ``ttl`` if nobody stored first (§3).

        Racing callers are not coordinated: each that misses runs its loader, the
        first to store wins, and the others answer the winner's value."""
        found = self.get(key)
        if found is not None:
            return found
        made = loader()
        if self.set_if_absent(key, made, ttl):
            return made
        # Somebody stored first — or stored and it has already expired.
        again = self.get(key)
        return made if again is None else again

    def lock(self, key: str, ttl: TtlLike, holder: str | None = None) -> Lease | None:
        """Take the lock ``key`` for ``ttl`` as ``holder`` (a fresh unique one when
        none is given); the lease, or ``None`` when somebody else holds it."""
        if holder == "":
            raise NotACacheArgument("a lock's holder is not empty")
        holder = holder if holder is not None else secrets.token_hex(16)
        lasting = _duration(ttl)
        if self._flag(*self._statements.lock(key, holder, lasting)):
            return Lease(key, holder, lasting, self)
        return None

    def _value(self, script: str, parameters: dict[str, Value]) -> Value:
        reply = self._connection.execute(script, parameters)
        answered = reply.outcomes[-1] if reply.outcomes else None
        if isinstance(answered, ValueOutcome):
            return answered.value
        if isinstance(answered, Done):
            return NONE
        raise ValueError(f"a cache statement answered {answered!r}")

    def _flag(self, script: str, parameters: dict[str, Value]) -> bool:
        found = self._value(script, parameters)
        if not isinstance(found, Bool):
            raise ValueError(f"a conditional write answered {found!r}")
        return found.value
