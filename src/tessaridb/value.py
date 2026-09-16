"""The seventeen value types the store carries.

Two distinctions are easy to lose in Python specifically, and this module keeps
both.

``none`` and ``null`` are different — the field is not present, versus the field
is present and holds nothing — and Python has one ``None`` for both, plus a habit
of using it for "missing" as well. Neither is spelled ``None`` here.

An integer is an ``i64`` and a decimal mantissa is an ``i128``, while Python's
``int`` is arbitrary precision. Nothing overflows, which means nothing complains:
the range checks live in the codec, at every fixed-width write, because the
alternative is a value silently truncated on the way out and read back happily by
this client's own decoder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

__all__ = [
    "Value",
    "NoneValue",
    "NullValue",
    "NONE",
    "NULL",
    "Bool",
    "Integer",
    "Float",
    "Decimal",
    "Text",
    "Bytes",
    "Duration",
    "Datetime",
    "Uuid",
    "Table",
    "RecordId",
    "IntegerId",
    "TextId",
    "UuidId",
    "BytesId",
    "Record",
    "Array",
    "Object",
    "Set",
    "Bound",
    "Unbounded",
    "Included",
    "Excluded",
    "Range",
    "Position",
    "Ring",
    "Polygon",
    "Shape",
    "Point",
    "Line",
    "PolygonShape",
    "MultiPoint",
    "MultiLine",
    "MultiPolygon",
    "Collection",
    "Geometry",
    "Regex",
]


class Value:
    """Sealed by convention: the concrete types below are the whole set.

    A tag is never reused for a different type and never renumbered, so this set
    grows only when the protocol's major version does.
    """

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class NoneValue(Value):
    """The field is not present."""


@dataclass(frozen=True, slots=True)
class NullValue(Value):
    """The field is present and holds nothing."""


NONE = NoneValue()
NULL = NullValue()


@dataclass(frozen=True, slots=True)
class Bool(Value):
    value: bool


@dataclass(frozen=True, slots=True)
class Integer(Value):
    value: int


@dataclass(frozen=True, slots=True)
class Float(Value):
    """A double, carried as its bits.

    ``0.0`` and ``-0.0`` are different values and a NaN is equal to itself, which
    is the opposite of what ``==`` says in Python. Comparison that matters to the
    protocol goes through the codec's own comparison, never through ``==``.
    """

    value: float


@dataclass(frozen=True, slots=True)
class Decimal(Value):
    """An unscaled value and a count of fractional digits — the two numbers that
    define an exact decimal, rather than an arithmetic library's in-memory
    layout. Deliberately not ``decimal.Decimal``: converting at the boundary
    would decide rounding questions the protocol does not ask.
    """

    mantissa: int
    scale: int


@dataclass(frozen=True, slots=True)
class Text(Value):
    value: str


@dataclass(frozen=True, slots=True)
class Bytes(Value):
    value: bytes


@dataclass(frozen=True, slots=True)
class Duration(Value):
    """Whole seconds plus nanoseconds in ``[0, 1e9)``.

    Deliberately not ``datetime.timedelta``: that type carries days, seconds and
    microseconds, so it cannot hold a nanosecond at all and would round one away
    without saying so.
    """

    seconds: int
    nanos: int


@dataclass(frozen=True, slots=True)
class Datetime(Value):
    """Seconds since the epoch plus nanoseconds, for the same reason: Python's
    ``datetime`` resolves to microseconds and would drop the last three digits.
    """

    seconds: int
    nanos: int


@dataclass(frozen=True, slots=True)
class Uuid(Value):
    value: bytes


@dataclass(frozen=True, slots=True)
class Table(Value):
    id: int


class RecordId:
    """A record's identity within its table. The four variants are fixed forever."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class IntegerId(RecordId):
    value: int


@dataclass(frozen=True, slots=True)
class TextId(RecordId):
    value: str


@dataclass(frozen=True, slots=True)
class UuidId(RecordId):
    value: bytes


@dataclass(frozen=True, slots=True)
class BytesId(RecordId):
    value: bytes


@dataclass(frozen=True, slots=True)
class Record(Value):
    table: int
    id: RecordId


@dataclass(frozen=True, slots=True)
class Array(Value):
    items: Sequence[Value] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Object(Value):
    """Fields by name.

    A client SHOULD emit them in name order, because that makes two equal values
    encode to equal bytes — a client-side convenience for comparing or caching
    its own encodings, and not a protocol requirement. The node re-normalises on
    decode either way, and a decoder must not rely on receiving any order.
    """

    fields: dict[str, Value] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Set(Value):
    """A distinct type from Array, because the collection kind is part of what
    the store held.
    """

    items: Sequence[Value] = field(default_factory=tuple)


class Bound:
    """One end of a range. Either end may itself hold a range."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Unbounded(Bound):
    pass


@dataclass(frozen=True, slots=True)
class Included(Bound):
    value: Value


@dataclass(frozen=True, slots=True)
class Excluded(Bound):
    value: Value


@dataclass(frozen=True, slots=True)
class Range(Value):
    start: Bound
    end: Bound


@dataclass(frozen=True, slots=True)
class Position:
    """A point on the sphere. Longitude FIRST, as RFC 7946 §3.1.1 fixes.

    The opposite order is the most common bug in geospatial code precisely
    because it is silent: a point in Paris becomes a point in the Indian Ocean,
    which is a perfectly valid place. Altitude is not carried by the protocol.
    """

    lon: float
    lat: float


Ring = Sequence[Position]


@dataclass(frozen=True, slots=True)
class Polygon:
    exterior: Ring = ()
    interiors: Sequence[Ring] = ()


class Shape:
    """One of the seven geometry kinds."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Point(Shape):
    position: Position


@dataclass(frozen=True, slots=True)
class Line(Shape):
    positions: Sequence[Position] = ()


@dataclass(frozen=True, slots=True)
class PolygonShape(Shape):
    polygon: Polygon = Polygon()


@dataclass(frozen=True, slots=True)
class MultiPoint(Shape):
    positions: Sequence[Position] = ()


@dataclass(frozen=True, slots=True)
class MultiLine(Shape):
    lines: Sequence[Ring] = ()


@dataclass(frozen=True, slots=True)
class MultiPolygon(Shape):
    polygons: Sequence[Polygon] = ()


@dataclass(frozen=True, slots=True)
class Collection(Shape):
    geometries: Sequence[Shape] = ()


@dataclass(frozen=True, slots=True)
class Geometry(Value):
    shape: Shape


@dataclass(frozen=True, slots=True)
class Regex(Value):
    """The pattern as written, uncompiled.

    A client MUST NOT compile it to decide whether it is valid: dialects disagree
    about what is valid, so a client that validates rejects patterns the node
    would have accepted, for a whole class of users at once.
    """

    pattern: str
