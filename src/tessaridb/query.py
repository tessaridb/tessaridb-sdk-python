"""The query builder — `spec/query-builder-v1.md`, the rendering contract.

**This is language, not protocol.** A client may implement the protocol
completely and ignore every word of it. The contract exists because the proof
that a builder produces the grammar the node's parser accepts lives in exactly
one place — the server's workspace — and no client may look there. Without a
written contract every language would arrive at its own plausible rendering, and
the divergence would surface as a user reporting that the same query behaves
differently depending on which client wrote it.

**A caller's value never reaches the statement text.** Every one becomes a bound
parameter: the text carries the reference and the value travels beside it,
encoded. A builder that formatted values in would destroy the property the whole
surface is designed around, and would do it invisibly, because the output still
looks correct.

**Names are the other half, and they are not values.** A table name, a field name
and a sort direction are grammar, so a parameter cannot supply one and each is
written into the text directly. That is safe only because each is checked first,
against a production deliberately narrower than the node's own lexer — a guard
that reasons about what a lexer would do has to be re-checked every time the
lexer changes, and one that accepts only ``[A-Za-z_][A-Za-z0-9_]*`` does not.

**A bad name raises where it was given, not at ``render``.** The contract says
the refusal reaches the caller rather than the node, and in Python the moment
that carries the most information is the call that made the mistake: the
traceback names the line. There are exactly two reasons — ``not-a-name`` and
``incomplete`` — and this builder never invents a third.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .errors import TessariError
from .value import Value

__all__ = [
    "BuilderError",
    "Operator",
    "Filter",
    "compare",
    "Rendered",
]


class BuilderError(TessariError):
    """A statement this builder will not render.

    ``what`` names the position — ``a table``, ``a field`` — and is part of the
    corpus's vocabulary rather than decoration: a refusal that says only *not a
    name* leaves the caller to find which of several names was meant.
    """

    def __init__(self, reason: str, what: str = "", name: str = "") -> None:
        if reason == "incomplete":
            super().__init__("a statement with no fields cannot be rendered")
        else:
            super().__init__(f"{name!r} is not a name, and {what} must be one")
        self.reason = reason
        self.what = what
        self.name = name


def check_name(what: str, name: str) -> str:
    """``name ::= ( ALPHA / "_" ) *( ALPHA / DIGIT / "_" )``, ASCII only.

    Refused rather than quoted into acceptance: quoting turns a caller's mistake
    into a statement that runs and means something else.
    """
    if not name:
        raise BuilderError("not-a-name", what, name)
    for at, character in enumerate(name):
        if character == "_" or ("a" <= character <= "z") or ("A" <= character <= "Z"):
            continue
        if at > 0 and "0" <= character <= "9":
            continue
        raise BuilderError("not-a-name", what, name)
    return name


class Operator(Enum):
    """The six the contract names, and no seventh.

    A closed set rather than a string, because rendering an unrecognised operator
    as empty text would produce a statement that parses as something else. A
    string that is not one of these is a ``ValueError`` and never a
    ``BuilderError`` — the contract forbids a third refusal reason.
    """

    EQ = "="
    NE = "!="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="


@dataclass(frozen=True)
class Rendered:
    """The statement and the values that travel beside it."""

    script: str
    parameters: dict[str, Value]


class Binder:
    """Numbers parameters ``p0``, ``p1``, … in binding order, counting from zero.

    Binding happens **during** rendering rather than before it, so the number a
    value gets is fixed by where its reference lands in the text. Numbering them
    first and rendering afterwards is the same thing only until a clause is
    rendered in an order the collection did not have.
    """

    def __init__(self) -> None:
        self.parameters: dict[str, Value] = {}

    def bind(self, value: Value) -> str:
        reference = f"p{len(self.parameters)}"
        self.parameters[reference] = value
        return f"${reference}"


class Filter:
    """A `WHERE` tree. Combine with ``&`` and ``|``.

    A conjunction and a disjunction are **fully parenthesised** and a bare
    comparison is not. The parentheses are not an aid to reading: a builder does
    not depend on the parser and therefore does not get to assume how ``AND`` and
    ``OR`` associate, and writing them all makes the tree the caller built the
    tree that runs.
    """

    def __and__(self, other: Filter) -> Filter:
        return _Junction("AND", self, other)

    def __or__(self, other: Filter) -> Filter:
        return _Junction("OR", self, other)

    def _render(self, binder: Binder) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class _Comparison(Filter):
    field: str
    operator: Operator
    value: Value

    def _render(self, binder: Binder) -> str:
        return f"{self.field} {self.operator.value} {binder.bind(self.value)}"


@dataclass(frozen=True)
class _Junction(Filter):
    word: str
    left: Filter
    right: Filter

    def _render(self, binder: Binder) -> str:
        # Depth-first, left to right — which is why the left side is rendered
        # into a name before the right side is asked for anything.
        left = self.left._render(binder)
        return f"({left} {self.word} {self.right._render(binder)})"


def compare(field: str, operator: Operator | str, value: Value) -> Filter:
    """``field <op> $pN``. The field is checked; the value is bound."""
    return _Comparison(check_name("a field", field), Operator(operator), value)


def render_object(binder: Binder, fields: dict[str, Value]) -> str:
    """``{ name: $p0, other: $p1 }`` — note the spaces immediately inside.

    Fields render in **ascending order of their names, compared byte by byte as
    UTF-8**, and not in the order the caller set them. Python's ``sorted`` on
    ``str`` compares by code point, and UTF-8 preserves code-point order, so
    ``sorted`` **is** the rule rather than an approximation of it.

    It is a rendering rule rather than an accident: two builders given the same
    fields in different orders must produce the same text and the same parameter
    numbering, which is what lets the corpus carry a set as an unordered JSON
    object.
    """
    written = []
    for name in sorted(fields):
        written.append(f"{name}: {binder.bind(fields[name])}")
    return "{ " + ", ".join(written) + " }"


def checked_fields(fields: dict[str, Value]) -> dict[str, Value]:
    if not fields:
        raise BuilderError("incomplete")
    for name in fields:
        check_name("a field", name)
    return dict(fields)
