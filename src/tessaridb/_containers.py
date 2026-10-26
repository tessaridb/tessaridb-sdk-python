"""A key holding a list, a hash, a set of members or a ranking — cache contract
1.1, §2a.

Writes are the node's container statements, atomic per key; reads are functions
over the key's value, so every call is still one statement. A read of a key that
is not there answers the empty form — an empty list, dict or set, ``0``,
``False`` — and an element, field, score or rank that is not there answers
``None``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Mapping, Sequence

from .value import Array, Bool, Float, Integer, NoneValue, Object, Set, Text, Value

if TYPE_CHECKING:
    from .cache import Cache


def _refused(reason: str) -> Exception:
    from .cache import NotACacheArgument

    return NotACacheArgument(reason)


def _some(items: Sequence | Mapping, reason: str) -> None:
    if len(items) == 0:
        raise _refused(reason)


def _whole(number: int, reason: str, *, negative: bool = False) -> None:
    if (
        isinstance(number, bool)
        or not isinstance(number, int)
        or (not negative and number < 0)
    ):
        raise _refused(reason)


def _finite(score: float) -> None:
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
    ):
        raise _refused("a score is a finite number")


class _Containers:
    """The §2a calls; mixed into :class:`~tessaridb.Cache`."""

    def list_push(
        self: Cache, key: str, values: Sequence[Value], front: bool = False
    ) -> int:
        """Push ``values`` at the back (or the front); the new length."""
        _some(values, "a push needs a value")
        return _integer(self._value(*self._statements.list_push(key, values, front)))

    def list_pop(self: Cache, key: str, front: bool = True) -> Value | None:
        """Take one element, or ``None`` when the list is not there."""
        return _present(self._value(*self._statements.list_pop(key, front, None)))

    def list_pop_many(
        self: Cache, key: str, count: int, front: bool = True
    ) -> list[Value]:
        """Take up to ``count`` elements, in the order taken."""
        _whole(count, "a count is not negative")
        return _items(self._value(*self._statements.list_pop(key, front, count)))

    def list_range(self: Cache, key: str, start: int, count: int) -> list[Value]:
        """``count`` elements from ``start``."""
        _whole(start, "a range starts at or after the first element")
        _whole(count, "a count is not negative")
        return _items(self._value(*self._statements.list_range(key, start, count)))

    def list_at(self: Cache, key: str, index: int) -> Value | None:
        """The element at ``index``, a negative one counted from the back."""
        _whole(index, "a position is a whole number", negative=True)
        return _present(self._value(*self._statements.list_at(key, index)))

    def list_len(self: Cache, key: str) -> int:
        """How many elements the list holds."""
        return _length(self._value(*self._statements.list_len(key)))

    def list_remove(
        self: Cache, key: str, value: Value, count: int | None = None
    ) -> int:
        """Remove ``value`` — every occurrence, or at most ``count``; how many went."""
        if count is not None:
            _whole(count, "a count is not negative")
        return _integer(self._value(*self._statements.list_remove(key, value, count)))

    def list_trim(self: Cache, key: str, start: int, stop: int) -> None:
        """Keep the elements from ``start`` to ``stop``, both included, either
        counted from the back when negative."""
        self._value(*self._statements.list_trim(key, start, stop))

    def hash_set(self: Cache, key: str, fields: Mapping[str, Value]) -> int:
        """Set ``fields``; how many were new."""
        _some(fields, "a hash write needs a field")
        return _integer(self._value(*self._statements.hash_set(key, fields)))

    def hash_get(self: Cache, key: str, field: str) -> Value | None:
        """One field, or ``None`` when it is not there."""
        return _present(self._value(*self._statements.hash_get(key, field)))

    def hash_get_all(self: Cache, key: str) -> dict[str, Value]:
        """Every field."""
        found = self._value(*self._statements.get(key))
        if isinstance(found, NoneValue):
            return {}
        if not isinstance(found, Object):
            raise ValueError(f"a hash read answered {found!r}")
        return dict(found.fields)

    def hash_remove(self: Cache, key: str, fields: Sequence[str]) -> int:
        """Remove ``fields``; how many went."""
        _some(fields, "a hash removal needs a field")
        return _integer(self._value(*self._statements.hash_remove(key, fields)))

    def hash_incr(self: Cache, key: str, field: str, by: int = 1) -> int:
        """Add ``by`` to a field; its new value."""
        _whole(by, "an increment is a whole number", negative=True)
        return _integer(self._value(*self._statements.hash_incr(key, field, by)))

    def hash_len(self: Cache, key: str) -> int:
        """How many fields the hash holds."""
        return _length(self._value(*self._statements.hash_len(key)))

    def members_add(self: Cache, key: str, members: Sequence[Value]) -> int:
        """Add ``members``; how many were new."""
        _some(members, "a set write needs a member")
        return _integer(self._value(*self._statements.members("ADD", key, members)))

    def members_remove(self: Cache, key: str, members: Sequence[Value]) -> int:
        """Remove ``members``; how many went."""
        _some(members, "a set removal needs a member")
        return _integer(self._value(*self._statements.members("REMOVE", key, members)))

    def members_has(self: Cache, key: str, member: Value) -> bool:
        """Whether ``member`` is in the set."""
        found = self._value(*self._statements.members_has(key, member))
        if not isinstance(found, Bool):
            raise ValueError(f"a membership answered {found!r}")
        return found.value

    def members_all(self: Cache, key: str) -> list[Value]:
        """Every member."""
        found = self._value(*self._statements.get(key))
        if isinstance(found, NoneValue):
            return []
        if not isinstance(found, Set):
            raise ValueError(f"a set read answered {found!r}")
        return list(found.items)

    def members_len(self: Cache, key: str) -> int:
        """How many members the set holds."""
        return _length(self._value(*self._statements.members_len(key)))

    def scores_add(self: Cache, key: str, scores: Mapping[str, float]) -> int:
        """Rank ``scores`` (member → score); how many members were new."""
        _some(scores, "a ranking write needs a member")
        for score in scores.values():
            _finite(score)
        return _integer(self._value(*self._statements.scores_add(key, scores)))

    def scores_incr(self: Cache, key: str, member: str, by: float) -> float:
        """Add ``by`` to a member's score; its new score."""
        _finite(by)
        found = self._value(*self._statements.scores_incr(key, member, by))
        if not isinstance(found, Float):
            raise ValueError(f"a score answered {found!r}")
        return found.value

    def scores_remove(self: Cache, key: str, members: Sequence[str]) -> int:
        """Remove ``members``; how many went."""
        _some(members, "a ranking removal needs a member")
        return _integer(self._value(*self._statements.scores_remove(key, members)))

    def scores_score(self: Cache, key: str, member: str) -> float | None:
        """A member's score, or ``None`` when it is not ranked."""
        found = self._value(*self._statements.scores_of("score", key, member))
        if isinstance(found, NoneValue):
            return None
        if not isinstance(found, Float):
            raise ValueError(f"a score answered {found!r}")
        return found.value

    def scores_rank(self: Cache, key: str, member: str) -> int | None:
        """A member's place from the lowest score, from 0, or ``None``."""
        found = self._value(*self._statements.scores_of("rank", key, member))
        if isinstance(found, NoneValue):
            return None
        return _integer(found)

    def scores_range(
        self: Cache, key: str, start: int, stop: int
    ) -> list[tuple[str, float]]:
        """The members at places ``start`` to ``stop``, both included, either
        counted from the highest when negative — lowest first."""
        return _ranked(self._value(*self._statements.scores_range(key, start, stop)))

    def scores_between(
        self: Cache, key: str, low: float | None = None, high: float | None = None
    ) -> list[tuple[str, float]]:
        """The members whose score is from ``low`` to ``high``, both included, an
        absent bound leaving that side open — lowest first."""
        return _ranked(self._value(*self._statements.scores_between(key, low, high)))


def _integer(found: Value) -> int:
    if not isinstance(found, Integer):
        raise ValueError(f"a container call answered {found!r}")
    return found.value


def _length(found: Value) -> int:
    return 0 if isinstance(found, NoneValue) else _integer(found)


def _present(found: Value) -> Value | None:
    return None if isinstance(found, NoneValue) else found


def _items(found: Value) -> list[Value]:
    if isinstance(found, NoneValue):
        return []
    if not isinstance(found, Array):
        raise ValueError(f"a container read answered {found!r}")
    return list(found.items)


def _ranked(found: Value) -> list[tuple[str, float]]:
    out = []
    for pair in _items(found):
        if (
            not isinstance(pair, Array)
            or len(pair.items) != 2
            or not isinstance(pair.items[0], Text)
            or not isinstance(pair.items[1], Float)
        ):
            raise ValueError(f"a ranking read answered {pair!r}")
        out.append((pair.items[0].value, pair.items[1].value))
    return out
