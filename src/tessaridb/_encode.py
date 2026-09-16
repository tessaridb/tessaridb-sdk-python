"""Writing a value (§4)."""

from __future__ import annotations

from ._bytes import ProtocolError, Writer
from ._tags import *  # noqa: F403 — the tag table, used by name throughout
from .value import (
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
    NoneValue,
    NullValue,
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


def encode(value: Value) -> bytes:
    """Encode one value to the bytes a `bytes` field of §3.4, §3.5 or §3.8 holds."""
    writer = Writer()
    _value(writer, value)
    return writer.bytes()


def _value(w: Writer, value: Value) -> None:
    match value:
        case NoneValue():
            w.u8(TAG_NONE)
        case NullValue():
            w.u8(TAG_NULL)
        case Bool(flag):
            w.u8(TAG_BOOL)
            w.u8(1 if flag else 0)
        case Integer(number):
            w.u8(TAG_NUMBER)
            w.u8(NUMBER_INTEGER)
            w.i64(number)
        case Float(number):
            w.u8(TAG_NUMBER)
            w.u8(NUMBER_FLOAT)
            w.double(number)
        case Decimal(mantissa, scale):
            w.u8(TAG_NUMBER)
            w.u8(NUMBER_DECIMAL)
            w.i128(mantissa)
            w.u32(scale)
        case Text(string):
            w.u8(TAG_STRING)
            w.text(string)
        case Bytes(raw):
            w.u8(TAG_BYTES)
            w.lenbytes(raw)
        case Duration(seconds, nanos):
            w.u8(TAG_DURATION)
            w.i64(seconds)
            w.nanos(nanos)
        case Datetime(seconds, nanos):
            w.u8(TAG_DATETIME)
            w.i64(seconds)
            w.nanos(nanos)
        case Uuid(raw):
            w.u8(TAG_UUID)
            w.fixed(raw, UUID_WIDTH, "a uuid")
        case Table(identifier):
            w.u8(TAG_TABLE)
            w.u32(identifier)
        case Record(table, identity):
            w.u8(TAG_RECORD)
            w.u32(table)
            _record_id(w, identity)
        case Array(items):
            w.u8(TAG_ARRAY)
            _items(w, items)
        case Set(items):
            w.u8(TAG_SET)
            _items(w, items)
        case Object(fields):
            w.u8(TAG_OBJECT)
            w.u32(len(fields))
            # Name order, so that two equal values encode to equal bytes. The
            # node re-normalises either way; this is what lets a client compare
            # or cache its own encodings.
            for name in sorted(fields):
                w.text(name)
                _value(w, fields[name])
        case Range(start, end):
            w.u8(TAG_RANGE)
            _bound(w, start)
            _bound(w, end)
        case Geometry(shape):
            w.u8(TAG_GEOMETRY)
            _shape(w, shape)
        case Regex(pattern):
            w.u8(TAG_REGEX)
            w.text(pattern)
        case _:
            raise ProtocolError(f"not a value this client can write: {type(value).__name__}")


def _items(w: Writer, items) -> None:
    w.u32(len(items))
    for item in items:
        _value(w, item)


def _bound(w: Writer, bound: Bound) -> None:
    match bound:
        case Unbounded():
            w.u8(BOUND_UNBOUNDED)
        case Included(value):
            w.u8(BOUND_INCLUDED)
            _value(w, value)
        case Excluded(value):
            w.u8(BOUND_EXCLUDED)
            _value(w, value)
        case _:
            raise ProtocolError(f"not a range bound: {type(bound).__name__}")


def _record_id(w: Writer, identity: RecordId) -> None:
    match identity:
        case IntegerId(number):
            w.u8(ID_INTEGER)
            w.i64(number)
        case TextId(string):
            w.u8(ID_TEXT)
            w.varbytes(string.encode("utf-8"))
        case UuidId(raw):
            w.u8(ID_UUID)
            w.fixed(raw, UUID_WIDTH, "a uuid record id")
        case BytesId(raw):
            w.u8(ID_BYTES)
            w.varbytes(raw)
        case _:
            raise ProtocolError(f"not a record id: {type(identity).__name__}")


def _position(w: Writer, position: Position) -> None:
    # Longitude first. A client that stores latitude first produces shapes that
    # encode, decode, index and render without complaint, and are wrong.
    w.double(position.lon)
    w.double(position.lat)


def _positions(w: Writer, positions) -> None:
    w.u32(len(positions))
    for position in positions:
        _position(w, position)


def _polygon(w: Writer, polygon: Polygon) -> None:
    _positions(w, polygon.exterior)
    w.u32(len(polygon.interiors))
    for ring in polygon.interiors:
        _positions(w, ring)


def _shape(w: Writer, shape: Shape) -> None:
    match shape:
        case Point(position):
            w.u8(SHAPE_POINT)
            _position(w, position)
        case Line(positions):
            w.u8(SHAPE_LINE)
            _positions(w, positions)
        case PolygonShape(polygon):
            w.u8(SHAPE_POLYGON)
            _polygon(w, polygon)
        case MultiPoint(positions):
            w.u8(SHAPE_MULTIPOINT)
            _positions(w, positions)
        case MultiLine(lines):
            w.u8(SHAPE_MULTILINE)
            w.u32(len(lines))
            for ring in lines:
                _positions(w, ring)
        case MultiPolygon(polygons):
            w.u8(SHAPE_MULTIPOLYGON)
            w.u32(len(polygons))
            for polygon in polygons:
                _polygon(w, polygon)
        case Collection(geometries):
            w.u8(SHAPE_COLLECTION)
            w.u32(len(geometries))
            for nested in geometries:
                _shape(w, nested)
        case _:
            raise ProtocolError(f"not a geometry: {type(shape).__name__}")
