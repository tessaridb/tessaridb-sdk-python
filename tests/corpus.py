"""Reading the shared conformance corpus, and comparing values the way the
protocol means rather than the way Python's ``==`` does.

The corpus lives in the protocol repository and is not vendored here. It is
produced by a separate implementation written from the specification alone, which
is the entire point: a codec that is wrong in the same way on both sides round
trips perfectly, so a suite written alongside this codec cannot catch what the
corpus catches.
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

from tessaridb.value import (
    Array,
    Bool,
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
    NONE,
    NULL,
    Object,
    Point,
    Polygon,
    PolygonShape,
    Position,
    Range,
    Record,
    Regex,
    Set,
    Table,
    Text,
    TextId,
    Unbounded,
    Uuid,
    UuidId,
)


def corpus_path(name: str) -> Path:
    override = os.environ.get("TESSARI_PROTOCOL_CONFORMANCE")
    if override:
        return Path(override) / name
    return Path(__file__).resolve().parents[2] / "tessaridb-protocol" / "conformance" / name


def read_corpus(name: str) -> dict:
    """Fails loudly when the corpus is absent. A suite that passes having found
    nothing to check reports coverage it does not have.
    """
    path = corpus_path(name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as why:
        raise AssertionError(
            f"the conformance corpus is required, not optional.\ntried: {path}\n{why}\n"
            "check out tessaridb-protocol beside this repository, or set "
            "TESSARI_PROTOCOL_CONFORMANCE to its conformance directory"
        ) from why


def double_from_bits(text: str) -> float:
    """The double whose bits are this hex — never a parsed decimal string."""
    raw = bytes.fromhex(text)
    assert len(raw) == 8, f"a double is 8 bytes, got {len(raw)}"
    return struct.unpack(">d", raw)[0]


def _one(node: dict):
    assert len(node) == 1, f"expected one tag, got {list(node)}"
    return next(iter(node.items()))


def value_of(node: dict):
    tag, body = _one(node)
    if tag == "none":
        return NONE
    if tag == "null":
        return NULL
    if tag == "bool":
        return Bool(body)
    if tag == "integer":
        return Integer(int(body))
    if tag == "float_bits":
        return Float(double_from_bits(body))
    if tag == "decimal":
        return Decimal(int(body["mantissa"]), body["scale"])
    if tag == "string":
        return Text(body)
    if tag == "bytes":
        return Bytes(bytes.fromhex(body))
    if tag == "duration":
        return Duration(int(body["seconds"]), body["nanos"])
    if tag == "datetime":
        return Datetime(int(body["seconds"]), body["nanos"])
    if tag == "uuid":
        return Uuid(bytes.fromhex(body))
    if tag == "table":
        return Table(body)
    if tag == "record":
        return Record(body["table"], record_id_of(body["id"]))
    if tag == "array":
        return Array(tuple(value_of(item) for item in body))
    if tag == "set":
        return Set(tuple(value_of(item) for item in body))
    if tag == "object":
        return Object({name: value_of(item) for name, item in body.items()})
    if tag == "range":
        return Range(bound_of(body["start"]), bound_of(body["end"]))
    if tag == "geometry":
        return Geometry(shape_of(body))
    if tag == "regex":
        return Regex(body)
    raise AssertionError(f"the corpus used a value tag this reader does not know: {tag}")


def record_id_of(node: dict):
    tag, body = _one(node)
    if tag == "int":
        return IntegerId(int(body))
    if tag == "text":
        return TextId(body)
    if tag == "uuid":
        return UuidId(bytes.fromhex(body))
    if tag == "bytes":
        return BytesId(bytes.fromhex(body))
    raise AssertionError(f"unknown record id tag in the corpus: {tag}")


def bound_of(node):
    # The two corpora spell an open end differently — the value corpus writes the
    # bare word, the JSON corpus writes it as a tagged object like every other
    # bound. Both are read, because a translator that knew only one would fail a
    # corpus for its notation rather than this client for its behaviour.
    if node == "unbounded":
        return Unbounded()
    tag, body = _one(node)
    if tag == "unbounded":
        return Unbounded()
    if tag == "included":
        return Included(value_of(body))
    if tag == "excluded":
        return Excluded(value_of(body))
    raise AssertionError(f"unknown range bound in the corpus: {tag}")


def position_of(node: dict) -> Position:
    return Position(double_from_bits(node["lon"]), double_from_bits(node["lat"]))


def positions_of(nodes) -> tuple[Position, ...]:
    return tuple(position_of(node) for node in nodes)


def polygon_of(node) -> Polygon:
    # Second notation divergence: the value corpus names the two parts, the JSON
    # corpus writes GeoJSON's flat list of rings with the exterior first.
    if isinstance(node, list):
        if not node:
            return Polygon()
        return Polygon(positions_of(node[0]), tuple(positions_of(ring) for ring in node[1:]))
    return Polygon(
        positions_of(node["exterior"]),
        tuple(positions_of(ring) for ring in node.get("interiors", ())),
    )


def shape_of(node: dict):
    tag, body = _one(node)
    if tag == "point":
        return Point(position_of(body))
    if tag == "line":
        return Line(positions_of(body))
    if tag == "polygon":
        return PolygonShape(polygon_of(body))
    if tag == "multipoint":
        return MultiPoint(positions_of(body))
    if tag == "multiline":
        return MultiLine(tuple(positions_of(ring) for ring in body))
    if tag == "multipolygon":
        return MultiPolygon(tuple(polygon_of(item) for item in body))
    if tag == "collection":
        return Collection(tuple(shape_of(item) for item in body))
    raise AssertionError(f"unknown geometry kind in the corpus: {tag}")


def _bits(number: float) -> bytes:
    return struct.pack(">d", number)


def same_value(left, right) -> bool:
    """Compares floats BY THEIR BITS.

    Python's ``==`` says ``nan != nan`` and says ``-0.0 == 0.0``, and the protocol
    disagrees with it on both: a NaN coordinate is equal to itself and a negative
    zero is a different coordinate. A dataclass ``==`` inherits both mistakes, so
    equality that matters goes through here.
    """
    if type(left) is not type(right):
        return False
    if isinstance(left, (Float,)):
        return _bits(left.value) == _bits(right.value)
    if isinstance(left, Position):
        return _bits(left.lon) == _bits(right.lon) and _bits(left.lat) == _bits(right.lat)
    if isinstance(left, (Array, Set)):
        return len(left.items) == len(right.items) and all(
            same_value(a, b) for a, b in zip(left.items, right.items)
        )
    if isinstance(left, Object):
        return set(left.fields) == set(right.fields) and all(
            same_value(value, right.fields[name]) for name, value in left.fields.items()
        )
    if isinstance(left, Range):
        return same_value(left.start, right.start) and same_value(left.end, right.end)
    if isinstance(left, (Included, Excluded)):
        return same_value(left.value, right.value)
    if isinstance(left, Geometry):
        return same_value(left.shape, right.shape)
    if isinstance(left, Point):
        return same_value(left.position, right.position)
    if isinstance(left, (Line, MultiPoint)):
        return _same_positions(left.positions, right.positions)
    if isinstance(left, PolygonShape):
        return _same_polygon(left.polygon, right.polygon)
    if isinstance(left, MultiLine):
        return len(left.lines) == len(right.lines) and all(
            _same_positions(a, b) for a, b in zip(left.lines, right.lines)
        )
    if isinstance(left, MultiPolygon):
        return len(left.polygons) == len(right.polygons) and all(
            _same_polygon(a, b) for a, b in zip(left.polygons, right.polygons)
        )
    if isinstance(left, Collection):
        return len(left.geometries) == len(right.geometries) and all(
            same_value(a, b) for a, b in zip(left.geometries, right.geometries)
        )
    return left == right


def _same_positions(left, right) -> bool:
    return len(left) == len(right) and all(same_value(a, b) for a, b in zip(left, right))


def _same_polygon(left: Polygon, right: Polygon) -> bool:
    return _same_positions(left.exterior, right.exterior) and len(left.interiors) == len(
        right.interiors
    ) and all(_same_positions(a, b) for a, b in zip(left.interiors, right.interiors))
