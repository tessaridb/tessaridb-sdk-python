"""GeoJSON, RFC 7946 — the one shape on this surface that is not lossy.

The rule worth restating at every boundary: coordinates are ``[longitude,
latitude]``. The opposite order is the most common bug in geospatial code
precisely because it is silent — a point in Paris becomes a point in the Indian
Ocean, which is a perfectly valid place.
"""

from __future__ import annotations

import math

from .errors import Malformed
from .value import (
    Collection,
    Line,
    MultiLine,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
    PolygonShape,
    Position,
    Shape,
)

__all__ = ["read_shape"]


SHAPES = {
    "Point": lambda body: Point(_position(body["coordinates"])),
    "LineString": lambda body: Line(tuple(_position(p) for p in body["coordinates"])),
    "Polygon": lambda body: PolygonShape(_polygon(body["coordinates"])),
    "MultiPoint": lambda body: MultiPoint(tuple(_position(p) for p in body["coordinates"])),
    "MultiLineString": lambda body: MultiLine(
        tuple(tuple(_position(p) for p in line) for line in body["coordinates"])
    ),
    "MultiPolygon": lambda body: MultiPolygon(tuple(_polygon(p) for p in body["coordinates"])),
    "GeometryCollection": lambda body: Collection(
        tuple(read_shape(nested) for nested in body["geometries"])
    ),
}


def read_shape(body: dict) -> Shape:
    try:
        build = SHAPES[body["type"]]
    except KeyError as why:
        raise Malformed(f"{body.get('type')!r} is not a GeoJSON type this build knows") from why
    return build(body)


def _position(pair) -> Position:
    # Longitude FIRST, as RFC 7946 fixes. The opposite order is the most common
    # bug in geospatial code precisely because it is silent.
    lon, lat = pair
    return Position(_coordinate(lon), _coordinate(lat))


def _coordinate(value) -> float:
    # A non-finite coordinate is written null: it cannot arise from a well-formed
    # shape and can arise from bytes, and the surface reports what it holds
    # rather than inventing a number.
    return math.nan if value is None else float(value)


def _polygon(rings) -> Polygon:
    walls = [tuple(_position(p) for p in ring) for ring in rings]
    return Polygon(walls[0] if walls else (), tuple(walls[1:]))
