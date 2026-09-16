"""What `POST /script` answers — the outcome shapes of §5.6.

The same statements produce the same outcomes on both transports and only the
encoding differs, but two things about this one do not exist on the wire and are
modelled here rather than folded into the wire's shape: a ``plan``, which the
wire carries as an access-path byte alone, and the absence of a names block,
because this surface has already resolved the names.

**A script is all-or-nothing on this route, and that describes the response
rather than the store.** If any statement fails the answer is a refusal carrying
that statement's error, and the outcomes of the statements that already succeeded
are not reported — but those statements **have taken effect and are durable**. So
a client must not describe a failed multi-statement script as *nothing happened*,
and must not retry one automatically: re-running it re-executes what already
succeeded, which turns one create into two. A caller who needs all-or-nothing
wraps the statements in ``BEGIN`` … ``COMMIT``, which is the only way to get it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._json import read_value
from .errors import Malformed
from .kind import Kind
from .outcome import (
    Complete,
    Correction,
    Corrections,
    Exact,
    Exactness,
    Inexact,
    NotConsulted,
    Note,
    Suggestion,
    Unstated,
)
from .value import NONE, Value

__all__ = [
    "Reading",
    "Plan",
    "ScriptRow",
    "ScriptOutcome",
    "ScriptDone",
    "ScriptRecords",
    "ScriptValue",
    "ScriptKeys",
    "ScriptRemoved",
    "ScriptUnknown",
    "read_outcome",
    "read_results",
]

#: Closed, and a client may switch on it exhaustively — unlike a plan's `source`
#: and `shape`, which are open word sets living in the same object.
PATHS = frozenset(
    {"record", "index", "ordered", "scan", "approximate", "graph", "join", "span", "materialised"}
)


@dataclass(frozen=True)
class Reading:
    """What the caller declares before reading an answer.

    One kind, used for a ``value`` outcome's value and for each record's value.
    Nothing is declared for an identity, deliberately: §5.7.1 forbids parsing one
    back, so there is no kind that could apply — and the corpus proves the point
    by holding ``"1"`` beside ``"ada"`` in one ``keys`` array, an integer identity
    and a text one that no single declaration could cover.
    """

    value: Kind | None = None


@dataclass(frozen=True)
class Plan:
    """``access`` is always present and always equal to the outcome's ``path``.

    Every other key is absent when the read had no answer for it, so a scan's
    plan is the two keys it knows rather than eight of which six say nothing.
    They are carried in ``details`` as the node wrote them, because ``source``
    and ``shape`` are **open** word sets: an unrecognised one is rendered, never
    switched on and never treated as an error.
    """

    access: str
    exactness: Exactness
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ScriptRow:
    """An element of ``records`` is a **pair**, not a value.

    A client that types the array as *the records* has the wrong shape and will
    read a field one level too high.
    """

    identity: str
    value: Value


class ScriptOutcome:
    """One per statement in the script, in order."""


@dataclass(frozen=True)
class ScriptDone(ScriptOutcome):
    pass


@dataclass(frozen=True)
class ScriptRecords(ScriptOutcome):
    path: str
    plan: Plan
    rows: tuple[ScriptRow, ...] = ()
    notes: tuple[Note, ...] = ()
    only: bool = False
    suggestion: Suggestion = field(default_factory=NotConsulted)


@dataclass(frozen=True)
class ScriptValue(ScriptOutcome):
    """``{"kind":"value"}`` with no ``value`` key is the language's ``none``;
    ``{"kind":"value","value":null}`` is a stored ``null``. JSON has one word for
    both, so the distinction is carried by the presence of the key."""

    value: Value | str


@dataclass(frozen=True)
class ScriptKeys(ScriptOutcome):
    """Identities as strings, each the **id half alone** — no ``table:`` prefix
    and no colon, because the statement named the table."""

    keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScriptRemoved(ScriptOutcome):
    count: int


@dataclass(frozen=True)
class ScriptUnknown(ScriptOutcome):
    """A kind this build has never seen — **not** an outcome carrying nothing.

    The difference is the remedy: upgrade, not shrug. A client must expose it and
    must not stop reading the list at the first one.
    """


def read_results(body: dict, reading: Reading) -> tuple[ScriptOutcome, ...]:
    results = body.get("results")
    if not isinstance(results, list):
        raise Malformed("a script answer carries a `results` array")
    return tuple(read_outcome(one, reading) for one in results)


def read_outcome(node: dict, reading: Reading) -> ScriptOutcome:
    kind = node.get("kind")
    if kind == "done":
        return ScriptDone()
    if kind == "value":
        if "value" not in node:
            return ScriptValue(NONE)
        if reading.value is None:
            raise Malformed("a value outcome needs the kind its field was declared with")
        return ScriptValue(read_value(node["value"], reading.value))
    if kind == "keys":
        keys = node.get("keys")
        if not isinstance(keys, list):
            raise Malformed("a keys outcome carries an array of strings")
        return ScriptKeys(tuple(keys))
    if kind == "removed":
        return ScriptRemoved(int(node["count"]))
    if kind == "unknown":
        return ScriptUnknown()
    if kind == "records":
        return _records(node, reading)
    raise Malformed(f"{kind!r} is not an outcome kind, and `unknown` is how a node says so")


def _records(node: dict, reading: Reading) -> ScriptRecords:
    path = node.get("path")
    if path not in PATHS:
        raise Malformed(f"{path!r} is not one of the nine access paths")
    rows = []
    for row in node.get("records", []):
        if reading.value is None:
            raise Malformed("a records outcome needs the kind its records were declared with")
        rows.append(ScriptRow(row["id"], read_value(row["value"], reading.value)))
    # `notes` is written only when non-empty and `only` only when true, so a
    # response with nothing to report is identical to what it would have been
    # before either existed. Both are absent-by-default rather than required.
    notes = tuple(Note(n["kind"], n["message"]) for n in node.get("notes", []))
    return ScriptRecords(
        path,
        _plan(node.get("plan", {})),
        tuple(rows),
        notes,
        bool(node.get("only", False)),
        _suggestion(node),
    )


def _plan(body: dict) -> Plan:
    """``exact`` is the one key of a plan that is always written, and its absence
    is the third state rather than the dull value.

    Every other key is present only when the read had an answer for it, and a key
    present only when interesting teaches a client that its absence means the
    dull value. Here the dull value is a claim, so a node that predates the field
    made none — and reading that as ``exact`` puts a promise into its mouth.
    """
    if "exact" not in body:
        exactness: Exactness = Unstated()
    elif body["exact"]:
        exactness = Exact()
    else:
        exactness = Inexact(body.get("inexact", ""))
    details = {name: v for name, v in body.items() if name not in ("access", "exact", "inexact")}
    return Plan(body.get("access", "scan"), exactness, details)


def _suggestion(node: dict) -> Suggestion:
    """Three states carried by two JSON facts: the presence of the key, and the
    length of the list inside it.

    **Present-and-empty is the claim; absent is the absence of one.** This is the
    one place on this transport where an absent key does not mean the dull value —
    it means the question was never asked, and a client that reads it as *nothing
    is near* reports a negative the node never checked.
    """
    if "suggestion" not in node:
        return NotConsulted()
    corrections = node["suggestion"].get("corrections", [])
    if not corrections:
        return Complete()
    return Corrections(tuple(Correction(c["typed"], c["instead"]) for c in corrections))
