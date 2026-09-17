"""The four statements a builder offers — `SELECT`, `CREATE`, `UPDATE`, `DELETE`,
over a single collection.

Anything else is written by the caller as a script and sent as one, which is
always available. A builder **must not** grow a clause this contract does not
carry, because a clause one language has and another does not is the divergence
the contract exists to prevent.

Counts — `START`, `LIMIT`, and a line window's two — are written into the text as
literals rather than bound as parameters: they are part of the statement's shape
rather than data. That is safe in a typed language because the type says so, and
it is safe here because every one of them is range-checked against ``u64`` at the
call that supplies it. Python's ``int`` is arbitrary precision, so nothing
overflows and nothing complains, and this is the same job §2.2's fixed-width
writes do in the codec.
"""

from __future__ import annotations

from .query import (
    Binder,
    BuilderError,
    Filter,
    Rendered,
    check_answerer,
    check_name,
    check_span,
    checked_fields,
    render_object,
)
from .value import Value

__all__ = ["select", "create", "update", "delete", "Select", "Create", "Update", "Delete"]

U64_MAX = 2**64 - 1


def _count(value: int, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= U64_MAX:
        raise ValueError(f"{what} is an unsigned 64-bit integer, got {value!r}")
    return value


class Select:
    """``SELECT … [WHERE] [ORDER BY] [START] [LIMIT] [STALENESS] [ANSWERED BY];``

    The last two decide **which node may answer** rather than what the answer
    holds, and they come last in that order because a node's parser accepts no
    other sequence.
    """

    def __init__(self, table: str) -> None:
        self._table = check_name("a table", table)
        self._items: list[str] = []
        self._where: Filter | None = None
        self._order: list[str] = []
        self._start: int | None = None
        self._limit: int | None = None
        self._staleness: str | None = None
        self._answered_by: str | None = None

    def field(self, name: str) -> Select:
        """A projection keeps **call order** — unlike an object body, which sorts."""
        self._items.append(check_name("a field", name))
        return self

    def lines(self, field: str, start: int, count: int) -> Select:
        """``string::lines(body, 0, 40) AS body`` — part of a long text field.

        The alias is the field's own name, so the field arrives under the name it
        always had and a caller's mapping does not change with the window.
        """
        name = check_name("a field", field)
        self._items.append(
            f"string::lines({name}, {_count(start, 'a window start')}, "
            f"{_count(count, 'a window count')}) AS {name}"
        )
        return self

    def where(self, filter: Filter) -> Select:
        """A second ``where`` **replaces** the first rather than combining with it.

        Silently ``AND``ing two would make a duplicated call look as though it had
        worked. A caller who wants both writes ``a & b``.
        """
        self._where = filter
        return self

    def order_by(self, field: str, descending: bool = False) -> Select:
        """The direction is always written out, including when it is the node's
        default, so the statement says what it does."""
        self._order.append(f"{check_name('a field', field)} {'DESC' if descending else 'ASC'}")
        return self

    def start(self, count: int) -> Select:
        self._start = _count(count, "a start")
        return self

    def limit(self, count: int) -> Select:
        self._limit = _count(count, "a limit")
        return self

    def staleness(self, span: str) -> Select:
        """How far behind the node answering this read may be — ``"30s"``, ``"1m30s"``.

        A candidate filter and never a marker: it says which nodes may answer at
        all, rather than labelling an answer as stale. A read no node can satisfy
        is refused by the node rather than quietly promoted to the one node that
        certainly can.
        """
        self._staleness = check_span(span)
        return self

    def answered_by(self, answerer: str) -> Select:
        """``"ANY"`` or ``"LEADER"`` — where the answer must come from.

        Not a tighter :meth:`staleness`: a follower at zero lag is *level*, not
        authoritative, so no freshness bound expresses *this must come from where
        writes are decided*.
        """
        self._answered_by = check_answerer(answerer)
        return self

    def render(self) -> Rendered:
        binder = Binder()
        projection = ", ".join(self._items) if self._items else "*"
        script = f"SELECT {projection} FROM {self._table}"
        if self._where is not None:
            script += f" WHERE {self._where._render(binder)}"
        if self._order:
            script += " ORDER BY " + ", ".join(self._order)
        if self._start is not None:
            script += f" START {self._start}"
        if self._limit is not None:
            script += f" LIMIT {self._limit}"
        if self._staleness is not None:
            script += f" STALENESS {self._staleness}"
        if self._answered_by is not None:
            script += f" ANSWERED BY {self._answered_by}"
        return Rendered(script + ";", binder.parameters)


class _Write:
    """The identity binds **first**, before any field.

    It travels as a parameter and never as text, so an identity that happens to
    spell a statement is a record with an unusual name.
    """

    def __init__(self, table: str, identity: Value | None) -> None:
        self._table = check_name("a table", table)
        self._identity = identity
        self._fields: dict[str, Value] = {}

    def set(self, fields: dict[str, Value] | None = None, **named: Value):
        """Fields to write. Given either as a mapping or as keywords — a field
        name that is not a Python identifier can only arrive the first way."""
        self._fields.update(fields or {})
        self._fields.update(named)
        return self


class Create(_Write):
    """``CREATE <table>:$p0 = { … };`` or ``CREATE <table> = { … };``

    Two forms, and the difference is whether the caller supplies the identity.
    """

    def render(self) -> Rendered:
        fields = checked_fields(self._fields)
        binder = Binder()
        subject = self._table
        if self._identity is not None:
            subject += f":{binder.bind(self._identity)}"
        return Rendered(f"CREATE {subject} = {render_object(binder, fields)};", binder.parameters)


class Update(_Write):
    """``UPDATE <table>:$p0 SET name = $p1, other = $p2;``

    ``SET`` changes the named fields only. The whole-record replacement form is
    not what a builder with a ``set`` method should quietly emit — a caller who
    wants a replacement asks for ``create``, where the word says so.
    """

    def render(self) -> Rendered:
        fields = checked_fields(self._fields)
        binder = Binder()
        if self._identity is None:
            raise BuilderError("incomplete")
        subject = f"{self._table}:{binder.bind(self._identity)}"
        assignments = ", ".join(f"{name} = {binder.bind(fields[name])}" for name in sorted(fields))
        return Rendered(f"UPDATE {subject} SET {assignments};", binder.parameters)


class Delete:
    """``DELETE <table>:$p0;`` — nothing to build, and a ``render`` anyway.

    The uniform surface is the point: a caller should not have to remember which
    one of the four is the exception.
    """

    def __init__(self, table: str, identity: Value) -> None:
        self._table = check_name("a table", table)
        self._identity = identity

    def render(self) -> Rendered:
        binder = Binder()
        return Rendered(
            f"DELETE {self._table}:{binder.bind(self._identity)};", binder.parameters
        )


def select(table: str) -> Select:
    return Select(table)


def create(table: str, identity: Value | None = None) -> Create:
    """With an identity the caller names the record; without one the store does."""
    return Create(table, identity)


def update(table: str, identity: Value) -> Update:
    return Update(table, identity)


def delete(table: str, identity: Value) -> Delete:
    return Delete(table, identity)
