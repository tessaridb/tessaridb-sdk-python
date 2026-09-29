"""A batch of events as the ``/series`` route reads it (protocol §5.9).

The body is one TessariQL value — an array of objects of literals — so each
value is rendered as TessariQL source, which §5.9 makes this client's job. The
kinds an event needs each have one spelling; every other kind is refused here,
before a byte is sent, rather than approximated into a value nobody meant.
"""

from __future__ import annotations

import datetime as _datetime
import math
from collections.abc import Sequence

from .errors import TessariError
from .value import (
    Array,
    Bool,
    Datetime,
    Decimal,
    Float,
    Integer,
    NoneValue,
    NullValue,
    Object,
    Text,
    Uuid,
    Value,
)

__all__ = ["NotAnEvent"]

_EPOCH = _datetime.datetime(1970, 1, 1, tzinfo=_datetime.timezone.utc)


class NotAnEvent(TessariError):
    """A batch holds something an event cannot carry; nothing was sent."""


def series_path(namespace: str, database: str, series: str) -> str:
    for segment in (namespace, database, series):
        if not segment.replace("_", "").isalnum() or not segment.isascii():
            raise ValueError(f"{segment!r} is not a name — these segments are [A-Za-z0-9_]+")
    return f"/series/{namespace}/{database}/{series}"


def batch(events: Sequence[Value]) -> str:
    """The body: one TessariQL array of the events."""
    rendered = []
    for event in events:
        if not isinstance(event, Object):
            raise NotAnEvent("an event is an object")
        rendered.append(literal(event))
    return "[" + ", ".join(rendered) + "]"


def literal(value: Value) -> str:
    """One value, spelled as §5.9 spells it."""
    if isinstance(value, NullValue):
        return "NULL"
    if isinstance(value, Bool):
        return "true" if value.value else "false"
    if isinstance(value, Integer):
        return str(value.value)
    if isinstance(value, Float):
        if not math.isfinite(value.value):
            raise NotAnEvent("a float that is not finite has no spelling")
        # `repr` always carries a `.` or an exponent, which keeps `1.0` a float.
        return repr(value.value)
    if isinstance(value, Decimal):
        return _decimal(value.mantissa, value.scale)
    if isinstance(value, Text):
        return _quoted(value.value)
    if isinstance(value, Datetime):
        return f"datetime '{_instant(value.seconds, value.nanos)}'"
    if isinstance(value, Uuid):
        hexed = value.value.hex()
        return f"uuid '{hexed[:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:]}'"
    if isinstance(value, Array):
        if any(isinstance(item, NoneValue) for item in value.items):
            raise NotAnEvent("an array cannot hold an absence")
        return "[" + ", ".join(literal(item) for item in value.items) + "]"
    if isinstance(value, Object):
        # A field holding none is left out, which is what absence means.
        fields = [
            f"{_quoted(name)}: {literal(held)}"
            for name, held in sorted(value.fields.items())
            if not isinstance(held, NoneValue)
        ]
        return "{ " + ", ".join(fields) + " }" if fields else "{}"
    raise NotAnEvent(
        "an event carries null, booleans, numbers, strings, datetimes, uuids, arrays and objects"
    )


def _quoted(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _decimal(mantissa: int, scale: int) -> str:
    digits = str(abs(mantissa))
    sign = "-" if mantissa < 0 else ""
    if scale == 0:
        return f"dec {sign}{digits}"
    digits = digits.rjust(scale + 1, "0")
    return f"dec {sign}{digits[:-scale]}.{digits[-scale:]}"


def _instant(seconds: int, nanos: int) -> str:
    try:
        moment = _EPOCH + _datetime.timedelta(seconds=seconds)
    except OverflowError as error:
        raise NotAnEvent("a datetime outside the years 1 to 9999") from error
    fraction = f".{nanos:09d}" if nanos else ""
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + fraction + "Z"
