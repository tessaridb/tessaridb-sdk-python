"""How an event's values are spelled for ``POST /series`` (protocol §5.9).

Offline. The node is the oracle for these spellings and `test_http_node` asks
it; this pins each one so a change to the renderer is seen here first.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tessaridb import (  # noqa: E402
    Array,
    Bool,
    Bytes,
    Datetime,
    Decimal,
    Float,
    Integer,
    NoneValue,
    NotAnEvent,
    NullValue,
    Object,
    Text,
    Uuid,
)
from tessaridb._events import batch, literal  # noqa: E402


class Spelling(unittest.TestCase):
    def test_each_kind_an_event_carries_has_its_spelling(self) -> None:
        cases = [
            (NullValue(), "NULL"),
            (Bool(True), "true"),
            (Integer(-12), "-12"),
            (Float(1.0), "1.0"),
            (Float(1.5e300), "1.5e+300"),
            (Decimal(-1234, 2), "dec -12.34"),
            (Decimal(5, 3), "dec 0.005"),
            (Text("it's \\ ok"), "'it\\'s \\\\ ok'"),
            (Datetime(1_790_676_000, 123_456_789), "datetime '2026-09-29T10:00:00.123456789Z'"),
            (Datetime(-1, 0), "datetime '1969-12-31T23:59:59Z'"),
            (
                Uuid(bytes.fromhex("0190a0b1000070008000000000000001")),
                "uuid '0190a0b1-0000-7000-8000-000000000001'",
            ),
            (
                Object({"odd key": Array((Bool(False),)), "gone": NoneValue()}),
                "{ 'odd key': [false] }",
            ),
        ]
        for value, spelled in cases:
            with self.subTest(value=value):
                self.assertEqual(literal(value), spelled)

    def test_a_kind_an_event_cannot_carry_is_refused_before_anything_is_sent(self) -> None:
        for value in (Float(float("nan")), Bytes(b"\x01"), Array((NoneValue(),))):
            with self.subTest(value=value), self.assertRaises(NotAnEvent):
                literal(value)
        with self.assertRaises(NotAnEvent):
            batch([Bool(True)])


if __name__ == "__main__":
    unittest.main()
