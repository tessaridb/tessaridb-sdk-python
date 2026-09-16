"""Declared kinds — what a value read over HTTP has to be told.

JSON has six types and the store has seventeen, so §5.7 is a decision rather than
a translation: for most of the table the type is **not recoverable from the JSON
alone**. ``"12.34"`` is a decimal or a string, ``"1h30m"`` is a duration or a
string, and ``users:7`` is the integer 7 or the text ``'7'``.

So the reader is told, and the caller reads the kind from the field's declaration
in the catalog. A reader that guessed instead would be right most of the time,
which is worse than being wrong all of it.

If you want types without carrying a catalog, use the wire protocol, where every
value carries its tag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

__all__ = [
    "Kind",
    "NullKind",
    "BoolKind",
    "IntegerKind",
    "FloatKind",
    "DecimalKind",
    "TextKind",
    "BytesKind",
    "DurationKind",
    "DatetimeKind",
    "UuidKind",
    "TableKind",
    "RecordKind",
    "RegexKind",
    "GeometryKind",
    "ArrayKind",
    "SetKind",
    "ObjectKind",
    "RangeKind",
]


class Kind:
    """What a JSON value is declared to be. Sealed by convention, like ``Value``."""


@dataclass(frozen=True)
class NullKind(Kind):
    """JSON ``null`` reads as the protocol's ``null`` under **any** kind, so this
    is for a field that holds nothing else."""


@dataclass(frozen=True)
class BoolKind(Kind):
    pass


@dataclass(frozen=True)
class IntegerKind(Kind):
    pass


@dataclass(frozen=True)
class FloatKind(Kind):
    pass


@dataclass(frozen=True)
class DecimalKind(Kind):
    """A quoted decimal is indistinguishable from a string that looks like a
    number — §5.7 names the ambiguity rather than hiding it."""


@dataclass(frozen=True)
class TextKind(Kind):
    pass


@dataclass(frozen=True)
class BytesKind(Kind):
    pass


@dataclass(frozen=True)
class DurationKind(Kind):
    pass


@dataclass(frozen=True)
class DatetimeKind(Kind):
    pass


@dataclass(frozen=True)
class UuidKind(Kind):
    pass


@dataclass(frozen=True)
class TableKind(Kind):
    """Reads as the table's **name**, a string, and not as a ``Table``.

    The wire carries a numeric id and the catalog holds the name; this surface
    carries only the name, and an id recovered by reversing a names block would
    be a number that means nothing outside the process that minted it.
    """


@dataclass(frozen=True)
class RecordKind(Kind):
    """Reads as the identity **string** the node wrote, and is never parsed back.

    ``users:7`` is the integer 7 and the text ``'7'`` written identically,
    ``users:a:b`` cannot be split on its colon, and a uuid id arrives without its
    hyphens while a bytes id arrives with a ``0x``. It is an identifier to
    display, log and pass back; a caller that needs the type reads the identity
    off the wire, where it carries its tag.
    """


@dataclass(frozen=True)
class RegexKind(Kind):
    """The pattern's source. The server does not execute it, and a client must
    not compile it with its own engine and present the result as this store's
    semantics."""


@dataclass(frozen=True)
class GeometryKind(Kind):
    pass


@dataclass(frozen=True)
class ArrayKind(Kind):
    """``of`` is one kind for every element, or one kind per element in order.

    An array whose elements are of different kinds is ordinary here — the store's
    arrays are not typed — so a single declared kind is a convenience rather than
    the general case.
    """

    of: Kind | Sequence[Kind]


@dataclass(frozen=True)
class SetKind(Kind):
    """A set arrives as an array and the collection type is not recoverable."""

    of: Kind | Sequence[Kind]


@dataclass(frozen=True)
class ObjectKind(Kind):
    """A declared kind per field. A field holding ``none`` is **omitted**, so a
    declared field that is not in the JSON is absent rather than null."""

    fields: dict[str, Kind] = field(default_factory=dict)


@dataclass(frozen=True)
class RangeKind(Kind):
    """The endpoints' kind. A range must never be modelled as a pair of endpoints
    without their bound kinds: ``1..5``, ``1..=5`` and ``1..`` are three
    different spans."""

    of: Kind
