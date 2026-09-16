"""Reading a value out of `POST /script`'s JSON at its declared kind — §5.7.

This surface is **lossy by construction** and the specification says so rather
than hiding it: a quoted decimal is indistinguishable from a string that looks
like a number, and a range object is indistinguishable from a stored object
carrying ``start`` and ``end``. The declared kind is what resolves it, and one
row stays lossy even then — see ``float`` below.

There is no writer here, and that is not an omission. A ``/script`` parameter is
a JSON string carrying *TessariQL source* rather than a value, so a client never
encodes a value on this surface at all.

**Python's own float traps do not bite here, and it is worth saying why.**
``float("nan")`` is ``7ff8000000000000`` — the canonical quiet NaN this protocol
writes — unlike Go's ``math.NaN()``, which carries a payload of 1 and re-encodes
to different bytes than the node sent. And ``json`` reads an integer as an exact
``int`` of arbitrary precision, so ``9223372036854775807`` survives, where a
reader that takes every JSON number as a double loses it silently.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from ._geojson import read_shape
from .errors import Malformed
from .kind import (
    ArrayKind,
    BoolKind,
    BytesKind,
    DatetimeKind,
    DecimalKind,
    DurationKind,
    FloatKind,
    GeometryKind,
    IntegerKind,
    Kind,
    NullKind,
    ObjectKind,
    RangeKind,
    RecordKind,
    RegexKind,
    SetKind,
    TableKind,
    TextKind,
    UuidKind,
)
from .value import (
    NONE,
    NULL,
    Array,
    Bool,
    Bytes,
    Datetime,
    Decimal,
    Duration,
    Excluded,
    Float,
    Geometry,
    Included,
    Integer,
    Object,
    Range,
    Regex,
    Set,
    Text,
    Unbounded,
    Uuid,
    Value,
)

__all__ = ["read_value"]

NANOS_PER_SECOND = 1_000_000_000
#: Largest unit first, and the two-letter units before the one-letter ones they
#: begin with — otherwise `ms` parses as `m` and leaves an `s` behind.
UNITS = (("ms", 1_000_000), ("us", 1_000), ("ns", 1), ("h", 3_600 * NANOS_PER_SECOND),
         ("m", 60 * NANOS_PER_SECOND), ("s", NANOS_PER_SECOND))


def read_value(node: object, kind: Kind) -> Value | str:
    """Read one JSON value at the kind its field was declared with.

    A ``table`` and a ``record`` come back as the **strings** the node wrote —
    see ``TableKind`` and ``RecordKind`` for why neither is reconstructed.
    """
    # JSON null is the protocol's null under every kind, including a declared
    # integer that happens to hold nothing.
    if node is None:
        return NULL
    if isinstance(kind, NullKind):
        raise Malformed(f"a field declared null holds {node!r}")
    if isinstance(kind, BoolKind):
        return Bool(_typed(node, bool, "a bool"))
    if isinstance(kind, IntegerKind):
        if isinstance(node, bool) or not isinstance(node, int):
            raise Malformed(f"a field declared integer holds {node!r}")
        return Integer(node)
    if isinstance(kind, FloatKind):
        return Float(_float(node))
    if isinstance(kind, DecimalKind):
        return _decimal(_typed(node, str, "a decimal"))
    if isinstance(kind, TextKind):
        return Text(_typed(node, str, "a string"))
    if isinstance(kind, BytesKind):
        return Bytes(bytes.fromhex(_typed(node, str, "bytes")))
    if isinstance(kind, DurationKind):
        return _duration(_typed(node, str, "a duration"))
    if isinstance(kind, DatetimeKind):
        return _datetime(_typed(node, str, "a datetime"))
    if isinstance(kind, UuidKind):
        return Uuid(bytes.fromhex(_typed(node, str, "a uuid").replace("-", "")))
    if isinstance(kind, (TableKind, RecordKind)):
        return _typed(node, str, "a table or record name")
    if isinstance(kind, RegexKind):
        return Regex(_typed(node, str, "a regex"))
    if isinstance(kind, GeometryKind):
        return Geometry(read_shape(_typed(node, dict, "a geometry")))
    if isinstance(kind, ArrayKind):
        return Array(tuple(_items(node, kind.of)))
    if isinstance(kind, SetKind):
        return Set(tuple(_items(node, kind.of)))
    if isinstance(kind, ObjectKind):
        return Object(_fields(_typed(node, dict, "an object"), kind))
    if isinstance(kind, RangeKind):
        body = _typed(node, dict, "a range")
        return Range(_bound(body.get("start"), kind.of), _bound(body.get("end"), kind.of))
    raise Malformed(f"no reader for the declared kind {kind!r}")


def _typed(node: object, want: type, what: str):
    if not isinstance(node, want) or (want is not bool and isinstance(node, bool)):
        raise Malformed(f"{what} is written as {want.__name__}, got {node!r}")
    return node


def _items(node: object, of):
    """One kind for every element, or one per element in order."""
    items = _typed(node, list, "an array")
    if isinstance(of, Kind):
        return [read_value(item, of) for item in items]
    if len(of) != len(items):
        raise Malformed(f"{len(of)} declared element kinds for {len(items)} elements")
    return [read_value(item, each) for item, each in zip(items, of)]


def _fields(node: dict, kind: ObjectKind) -> dict[str, Value]:
    """A declared field that is not in the JSON holds ``none``.

    This is why §5.7 does not mark ``object`` lossy even though a ``none`` field
    is omitted from the body: the declaration says the field exists, and absence
    is then the encoding rather than a loss. It is also the distinction JSON
    cannot draw on its own — an omitted key is ``none`` and a key holding
    ``null`` is ``null``, and a reader that produced the same thing for both
    would make a field that was never set read identically to one that was set to
    nothing.
    """
    fields: dict[str, Value] = {}
    for name, declared in kind.fields.items():
        fields[name] = read_value(node[name], declared) if name in node else NONE
    return fields


def _bound(node: object, of: Kind):
    body = _typed(node, dict, "a range endpoint")
    bound = body.get("bound")
    if bound == "unbounded":
        # An unbounded end carries no `value` key at all, which is what keeps an
        # open end distinct from an end holding null.
        return Unbounded()
    if "value" not in body:
        raise Malformed(f"a {bound!r} endpoint carries no value")
    if bound == "included":
        return Included(read_value(body["value"], of))
    if bound == "excluded":
        return Excluded(read_value(body["value"], of))
    raise Malformed(f"a range bound is included, excluded or unbounded, not {bound!r}")


def _float(node: object) -> float:
    if isinstance(node, str):
        # Quoted, because JSON has no spelling for these and an unquoted one
        # produces a document most parsers reject.
        if node == "inf":
            return math.inf
        if node == "-inf":
            return -math.inf
        if node == "NaN":
            return math.nan
        raise Malformed(f"a float is a number or inf/-inf/NaN, got {node!r}")
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        raise Malformed(f"a float is a number, got {node!r}")
    # A float is written positionally with no trailing `.0`, so one holding a
    # whole number arrives as a JSON integer. That is the row's lossiness, and
    # `-0.0` is past recovering: the value is normalised before it is written,
    # so `-0.0` and `0.0` are one value and it writes as `0`.
    return float(node)


def _decimal(text: str) -> Decimal:
    whole, dot, fraction = text.partition(".")
    if not dot:
        fraction = ""
    digits = whole + fraction
    try:
        return Decimal(int(digits), len(fraction))
    except ValueError as why:
        raise Malformed(f"{text!r} is not a decimal: {why}") from why


def _duration(text: str) -> Duration:
    """A decomposition over exactly six units, largest first, zero units omitted.

    Negative spans are a leading ``-`` on the magnitude, so ``-500ms`` is one
    second back and five hundred million nanoseconds forward once it is
    normalised — which is the form the protocol carries.
    """
    body = text[1:] if text.startswith("-") else text
    if not body:
        raise Malformed(f"{text!r} is not a duration")
    total = 0
    at = 0
    while at < len(body):
        start = at
        while at < len(body) and body[at].isdigit():
            at += 1
        if at == start:
            raise Malformed(f"{text!r} is not a duration: a unit with no count")
        count = int(body[start:at])
        for unit, nanos in UNITS:
            if body.startswith(unit, at):
                total += count * nanos
                at += len(unit)
                break
        else:
            raise Malformed(f"{text!r} is not a duration: no unit at {at}")
    if text.startswith("-"):
        total = -total
    # Floor division, so the nanoseconds are always the forward part of the span.
    return Duration(total // NANOS_PER_SECOND, total % NANOS_PER_SECOND)


def _datetime(text: str) -> Datetime:
    """Always UTC, always ending ``Z``, with up to nine fractional digits.

    Parsed by hand because the fraction reaches nanoseconds and every datetime
    type in the standard library stops at microseconds — reading it through one
    would drop the last three digits without saying so.
    """
    if not text.endswith("Z"):
        raise Malformed(f"a datetime is UTC and ends in Z, got {text!r}")
    instant, dot, fraction = text[:-1].partition(".")
    if len(fraction) > 9:
        raise Malformed(f"a datetime carries at most nine fractional digits: {text!r}")
    try:
        when = datetime.strptime(instant, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError as why:
        raise Malformed(f"{text!r} is not an RFC 3339 instant: {why}") from why
    nanos = int(fraction.ljust(9, "0")) if dot else 0
    return Datetime(int(when.timestamp()), nanos)
