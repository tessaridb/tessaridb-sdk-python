"""The JSON corpus — 59 value spellings read at their declared kind.

This corpus is **decode-only**, and it says why: a client never encodes a value
on this surface, since a `/script` parameter carries TessariQL source rather than
JSON. So it is weaker than the value corpus by construction — there is no writer
for a wrong reader to agree with — and what it does catch is every place the JSON
is lossy and the declared kind is what restores the value.

Two rows are compared by rendering the corpus's value **forward** into the string
the node writes, because those are the two the contract forbids reading back: a
table arrives as its name and a record as its identity, and §5.7.1 states in so
many words that a client must not parse the second into a typed record id.
Forward is the one direction that is defined.
"""

from __future__ import annotations

import math
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from corpus import read_corpus, same_value, value_of  # noqa: E402

from tessaridb import NONE, NULL, Float, Object  # noqa: E402
from tessaridb._json import read_value  # noqa: E402
from tessaridb.kind import (  # noqa: E402
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

CASES_WHEN_WRITTEN = 59

SIMPLE = {
    "null": NullKind,
    "bool": BoolKind,
    "integer": IntegerKind,
    "float_bits": FloatKind,
    "decimal": DecimalKind,
    "string": TextKind,
    "bytes": BytesKind,
    "duration": DurationKind,
    "datetime": DatetimeKind,
    "uuid": UuidKind,
    "table": TableKind,
    "record": RecordKind,
    "regex": RegexKind,
    "geometry": GeometryKind,
}


def kind_of(node: dict) -> Kind:
    """The declared kind a caller would have read out of the catalog.

    Derived here from the corpus's own description of the value, which is the
    catalog's stand-in: the whole point of §5.7 is that the JSON alone does not
    carry it.
    """
    (tag, body), = node.items()
    if tag in SIMPLE:
        return SIMPLE[tag]()
    if tag == "none":
        # A field holding none is omitted from the body, so the kind declared
        # for it is never consulted — but the declaration is what says the field
        # exists at all, which is how the value comes back.
        return NullKind()
    if tag == "array":
        return ArrayKind(tuple(kind_of(item) for item in body))
    if tag == "set":
        return SetKind(tuple(kind_of(item) for item in body))
    if tag == "object":
        return ObjectKind({name: kind_of(v) for name, v in body.items()})
    if tag == "range":
        for end in (body["start"], body["end"]):
            ((bound, inner),) = end.items()
            if bound != "unbounded":
                return RangeKind(kind_of(inner))
        return RangeKind(NullKind())
    raise AssertionError(f"the corpus carries a value this translator does not know: {tag}")


def identity_of(node: dict, names: dict[str, str]) -> str:
    """§5.7.1's ``table:id``, rendered forward.

    The id half is written in the **identity** syntax rather than the value
    syntax, which is where two rows of §5.7's table disagree with it on purpose:
    a uuid id loses its hyphens and a bytes id gains a ``0x``.
    """
    table = node["table"]
    ((form, value),) = node["id"].items()
    if form == "int":
        spelled = str(value)
    elif form == "text":
        spelled = value
    elif form == "uuid":
        spelled = value
    elif form == "bytes":
        spelled = "0x" + value
    else:
        raise AssertionError(f"an id form this translator does not know: {form}")
    name = names.get(str(table))
    return f"{name}:{spelled}" if name else f"<record {table}:{spelled}>"


class JsonCorpus(unittest.TestCase):
    def setUp(self) -> None:
        self.corpus = read_corpus("json-v1.json")
        self.names = self.corpus["names"]
        self.assertGreaterEqual(
            len(self.corpus["cases"]),
            CASES_WHEN_WRITTEN,
            "the corpus shrank — a suite cannot get stronger by losing vectors",
        )

    def test_every_spelling_reads_back_at_its_declared_kind(self) -> None:
        unrecoverable = {"float-negative-zero"}
        for case in self.corpus["cases"]:
            if case.get("omitted") or case["name"] in unrecoverable:
                continue
            with self.subTest(case["name"]):
                ((tag, body),) = case["value"].items()
                got = read_value(case["json"], kind_of(case["value"]))
                if tag == "table":
                    self.assertEqual(got, self.names.get(str(body), f"<table {body}>"))
                elif tag == "record":
                    self.assertEqual(got, identity_of(body, self.names))
                else:
                    self.assertTrue(
                        same_value(value_of(case["value"]), got),
                        f"read back as {got!r}",
                    )

    def test_none_has_no_spelling_because_absence_is_the_encoding(self) -> None:
        # Not the same as null, and JSON has one word for both — so the
        # distinction is carried by the presence of the key.
        omitted = [c for c in self.corpus["cases"] if c.get("omitted")]
        self.assertTrue(omitted, "the corpus carries the case that states this")
        for case in omitted:
            self.assertNotIn("json", case, "an omitted value has no JSON spelling")

        read = read_value(
            {"explicit": None},
            ObjectKind({"absent": IntegerKind(), "explicit": IntegerKind()}),
        )
        self.assertIsInstance(read, Object)
        self.assertIs(read.fields["absent"], NONE, "an omitted key is none, not null")
        self.assertEqual(read.fields["explicit"], NULL)

    def test_a_negative_zero_is_not_recoverable_and_this_client_says_so(self) -> None:
        # Not a rendering choice: a float is normalised when the value is built,
        # so -0.0 and 0.0 are one value and it writes as 0. The corpus carries
        # the case to make a client say so rather than quietly pass.
        case = next(c for c in self.corpus["cases"] if c["name"] == "float-negative-zero")
        self.assertEqual(case["json"], 0)
        wanted = value_of(case["value"])
        self.assertEqual(struct.pack(">d", wanted.value).hex(), "8000000000000000")
        got = read_value(case["json"], FloatKind())
        self.assertEqual(got, Float(0.0))
        self.assertEqual(
            struct.pack(">d", got.value).hex(),
            "0000000000000000",
            "no reader can tell -0.0 from +0.0 here, and one that claimed to would be lying",
        )

    def test_the_canonical_quiet_nan_is_pythons_own(self) -> None:
        # Go's math.NaN() carries a payload of 1 and re-encodes to different
        # bytes than the node sent; Python's float("nan") does not, and neither
        # does JavaScript's. Asserted rather than assumed, because the cost of
        # being wrong is a value that disagrees with itself across transports.
        got = read_value("NaN", FloatKind())
        self.assertTrue(math.isnan(got.value))
        self.assertEqual(struct.pack(">d", got.value).hex(), "7ff8000000000000")

    def test_the_corpus_names_the_one_thing_it_deliberately_does_not_fix(self) -> None:
        # A set's order across types is deterministic and its rank is not
        # published, so no vector can fix it and this client must not rely on it.
        gaps = {gap["case"] for gap in self.corpus["gaps"]}
        self.assertIn("set-order-across-types", gaps)


if __name__ == "__main__":
    unittest.main()
