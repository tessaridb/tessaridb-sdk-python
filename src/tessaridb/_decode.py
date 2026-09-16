"""Reading a value (§4).

An unknown tag is an error, never a guess. A codec that inferred the type from
what follows would read a newer format as a plausible wrong value, and nothing
downstream could tell.
"""

from __future__ import annotations

from ._bytes import ProtocolError, Reader
from ._tags import *  # noqa: F403 — the tag table, used by name throughout
from .value import (
    NONE,
    NULL,
    Array,
    Bool,
    Bound,
    Bytes,
    BytesId,
    Collection,
    Datetime,
    Decimal,
    Duration,
    Excluded,
    Float,
    Geometry,
    Included,
    Integer,
    IntegerId,
    Line,
    MultiLine,
    MultiPoint,
    MultiPolygon,
    Object,
    Point,
    Polygon,
    PolygonShape,
    Position,
    Range,
    Record,
    RecordId,
    Regex,
    Set,
    Shape,
    Table,
    Text,
    TextId,
    Unbounded,
    Uuid,
    UuidId,
    Value,
)


def decode(raw: bytes) -> Value:
    """Decode exactly one value from a payload.

    The buffer must be exhausted afterwards: bytes remaining are an error, not
    something to ignore.
    """
    reader = Reader(raw)
    value = _value(reader)
    if not reader.exhausted:
        raise ProtocolError(f"{reader.remaining} trailing byte(s) after a value")
    return value


def _value(r: Reader) -> Value:
    tag = r.u8("a value tag")
    if tag == TAG_NONE:
        return NONE
    if tag == TAG_NULL:
        return NULL
    if tag == TAG_BOOL:
        return Bool(r.u8("a bool") != 0)
    if tag == TAG_NUMBER:
        return _number(r)
    if tag == TAG_STRING:
        return Text(r.text("a string"))
    if tag == TAG_BYTES:
        return Bytes(r.lenbytes("bytes"))
    if tag == TAG_DURATION:
        return Duration(r.i64("a duration's seconds"), r.nanos("a duration's nanoseconds"))
    if tag == TAG_DATETIME:
        return Datetime(r.i64("a datetime's seconds"), r.nanos("a datetime's nanoseconds"))
    if tag == TAG_UUID:
        return Uuid(r.fixed(UUID_WIDTH, "a uuid"))
    if tag == TAG_TABLE:
        return Table(r.u32("a table id"))
    if tag == TAG_RECORD:
        return Record(r.u32("a record's table id"), _record_id(r))
    if tag == TAG_ARRAY:
        return Array(_items(r, "an array"))
    if tag == TAG_SET:
        return Set(_items(r, "a set"))
    if tag == TAG_OBJECT:
        count = r.u32("an object's field count")
        fields: dict[str, Value] = {}
        for _ in range(count):
            # The name is read into a variable FIRST, deliberately. Python
            # evaluates an assignment's right-hand side before the subscript it
            # assigns into, so `fields[r.text(...)] = _value(r)` reads the value
            # bytes as the name and then the next tag as a value — a stream
            # decoder wrong by one field, on objects only.
            name = r.text("an object field name")
            fields[name] = _value(r)
        return Object(fields)
    if tag == TAG_RANGE:
        return Range(_bound(r), _bound(r))
    if tag == TAG_GEOMETRY:
        return Geometry(_shape(r))
    if tag == TAG_REGEX:
        return Regex(r.text("a regex"))
    raise ProtocolError(f"unknown value tag {tag:#04x}")


def _number(r: Reader) -> Value:
    kind = r.u8("a number kind")
    if kind == NUMBER_INTEGER:
        return Integer(r.i64("an integer"))
    if kind == NUMBER_FLOAT:
        return Float(r.double("a float"))
    if kind == NUMBER_DECIMAL:
        return Decimal(r.i128("a decimal mantissa"), r.u32("a decimal scale"))
    raise ProtocolError(f"unknown number kind {kind:#04x}")


def _items(r: Reader, what: str) -> tuple[Value, ...]:
    count = r.u32(f"{what} count")
    return tuple(_value(r) for _ in range(count))


def _bound(r: Reader) -> Bound:
    kind = r.u8("a range bound")
    if kind == BOUND_UNBOUNDED:
        return Unbounded()
    if kind == BOUND_INCLUDED:
        return Included(_value(r))
    if kind == BOUND_EXCLUDED:
        return Excluded(_value(r))
    raise ProtocolError(f"unknown range bound {kind:#04x}")


def _record_id(r: Reader) -> RecordId:
    tag = r.u8("a record id tag")
    if tag == ID_INTEGER:
        return IntegerId(r.i64("an integer record id"))
    if tag == ID_TEXT:
        raw = r.varbytes("a text record id")
        try:
            return TextId(raw.decode("utf-8"))
        except UnicodeDecodeError as why:
            raise ProtocolError(f"a text record id is not UTF-8: {why}") from why
    if tag == ID_UUID:
        return UuidId(r.fixed(UUID_WIDTH, "a uuid record id"))
    if tag == ID_BYTES:
        return BytesId(r.varbytes("a bytes record id"))
    raise ProtocolError(f"unknown record id tag {tag:#04x}")


def _position(r: Reader) -> Position:
    return Position(r.double("a longitude"), r.double("a latitude"))


def _positions(r: Reader) -> tuple[Position, ...]:
    return tuple(_position(r) for _ in range(r.u32("a position count")))


def _polygon(r: Reader) -> Polygon:
    exterior = _positions(r)
    interiors = tuple(_positions(r) for _ in range(r.u32("an interior ring count")))
    return Polygon(exterior, interiors)


def _shape(r: Reader) -> Shape:
    kind = r.u8("a geometry kind")
    if kind == SHAPE_POINT:
        return Point(_position(r))
    if kind == SHAPE_LINE:
        return Line(_positions(r))
    if kind == SHAPE_POLYGON:
        return PolygonShape(_polygon(r))
    if kind == SHAPE_MULTIPOINT:
        return MultiPoint(_positions(r))
    if kind == SHAPE_MULTILINE:
        return MultiLine(tuple(_positions(r) for _ in range(r.u32("a line count"))))
    if kind == SHAPE_MULTIPOLYGON:
        return MultiPolygon(tuple(_polygon(r) for _ in range(r.u32("a polygon count"))))
    if kind == SHAPE_COLLECTION:
        return Collection(tuple(_shape(r) for _ in range(r.u32("a geometry count"))))
    raise ProtocolError(f"unknown geometry kind {kind:#04x}")
