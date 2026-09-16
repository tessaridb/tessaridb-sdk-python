"""What an answer says — the outcome model of §3.5.

One outcome per statement in the script, in order. Six kinds, and three of the
fields inside a ``Records`` outcome are absent on an older node while only two of
them default: read ``Exactness`` before changing any of it.

The bytes that produce these live in ``_answer``; this module is what a caller
holds afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .value import Value

__all__ = [
    "Note",
    "Exactness",
    "Exact",
    "Inexact",
    "Unstated",
    "Suggestion",
    "NotConsulted",
    "Complete",
    "Correction",
    "Corrections",
    "Row",
    "Outcome",
    "Done",
    "Records",
    "ValueOutcome",
    "Keys",
    "Removed",
    "Unknown",
]

@dataclass(frozen=True)
class Note:
    """A kind and a message, not a structure.

    The kinds are an **open** set. A client that refuses an unfamiliar one is
    non-conforming: kinds are added the same way outcome tags are.
    """

    kind: str
    message: str


class Exactness:
    """Three states, and a client whose type for this is a boolean has already
    lost the distinction.

    Every other appended field in a Records body follows the rule that absent
    means the default, because the default is what an older node's read actually
    *was*. This field breaks that pattern deliberately: a node that predates it
    did not serve exact answers and forget to say so — it made **no claim at
    all**. Reading an absent exactness as ``Exact`` would put a promise into the
    mouth of a node that never made one, on the one property whose entire purpose
    is that a caller never has to infer it.
    """


@dataclass(frozen=True)
class Exact(Exactness):
    """The answer is provably the records the question names."""


@dataclass(frozen=True)
class Inexact(Exactness):
    """It is not, and here is why — the node's own words.

    Carried on the wire rather than derived from the access path by the client. A
    client that phrased it itself would be describing a read it did not perform,
    and would go on describing it after the node's own wording changed.
    """

    reason: str


@dataclass(frozen=True)
class Unstated(Exactness):
    """The node said nothing. This is not ``Exact``."""


class Suggestion:
    """What the node thinks the query might have meant — advice about a
    *different* question, never an answer to the one that was asked."""


@dataclass(frozen=True)
class NotConsulted(Suggestion):
    """No term dictionary was consulted for this read.

    This is what nearly every read on this wire carries, because a suggestion
    needs a term dictionary and only a search index has one. That is the field
    working, not the field missing.
    """


@dataclass(frozen=True)
class Complete(Suggestion):
    """One was consulted, and it holds every term the query named.

    Not the same as ``NotConsulted``: this is a claim about the collection, that
    one is the absence of a claim. A client that renders both as *no suggestions*
    reports a negative the node never checked, on every read of an unindexed
    field.
    """


@dataclass(frozen=True)
class Correction:
    """``typed`` is the term as the query asked for it **after analysis** —
    lowercased, folded and stemmed by the field's analyzer — and not the raw
    substring the reader wrote."""

    typed: str
    instead: str


@dataclass(frozen=True)
class Corrections(Suggestion):
    """One was consulted, and here is what it holds instead.

    Nearness is the node's own. A client that ran a looser walk over terms it had
    seen would suggest words the node's own fuzzy operator refuses to match, and
    the reader would be offered a correction that returns nothing.
    """

    items: tuple[Correction, ...]


@dataclass(frozen=True)
class Row:
    """A record identity is **text**, exactly as the store spells it.

    A client that re-parses identities into a typed value has created a second
    spelling authority that can disagree with the store's.
    """

    identity: str
    value: Value


class Outcome:
    """One per statement in the script, in order."""


@dataclass(frozen=True)
class Done(Outcome):
    pass


@dataclass(frozen=True)
class Records(Outcome):
    path: str
    names: dict[int, str]
    rows: tuple[Row, ...]
    notes: tuple[Note, ...] = ()
    only: bool = False
    exactness: Exactness = field(default_factory=Unstated)
    suggestion: Suggestion = field(default_factory=NotConsulted)


@dataclass(frozen=True)
class ValueOutcome(Outcome):
    names: dict[int, str]
    value: Value


@dataclass(frozen=True)
class Keys(Outcome):
    keys: tuple[str, ...]


@dataclass(frozen=True)
class Removed(Outcome):
    count: int


@dataclass(frozen=True)
class Unknown(Outcome):
    """A tag this build does not know. Surfaced, never dropped — saying so is
    honest where guessing at its content is not, and a client must not stop
    reading at the first one."""

    tag: int
    body: bytes


