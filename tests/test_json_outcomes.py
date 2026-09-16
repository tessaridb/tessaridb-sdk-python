"""The twenty outcome shapes of §5.6, read from the shared corpus.

Split from the value half because they answer different questions: those check
how one value is spelled, these check the object a statement's result arrives in
— and three of its fields carry a distinction that an absent key would otherwise
erase.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from corpus import read_corpus, same_value, value_of  # noqa: E402
from test_json_corpus import identity_of, kind_of  # noqa: E402

from tessaridb import NONE  # noqa: E402
from tessaridb.outcome import (  # noqa: E402
    Complete,
    Corrections,
    Exact,
    Inexact,
    NotConsulted,
    Unstated,
)
from tessaridb.script import (  # noqa: E402
    Reading,
    ScriptDone,
    ScriptRemoved,
    ScriptUnknown,
    read_outcome,
    read_results,
)

class Outcomes(unittest.TestCase):
    """The twenty outcome shapes of §5.6."""

    def setUp(self) -> None:
        self.corpus = read_corpus("json-v1.json")
        self.names = self.corpus["names"]
        self.assertGreaterEqual(len(self.corpus["outcomes"]), 20)

    def test_every_outcome_shape_reads_as_what_the_corpus_describes(self) -> None:
        for case in self.corpus["outcomes"]:
            with self.subTest(case["name"]):
                self.check(case)

    def check(self, case: dict) -> None:
        ((tag, body),) = case["outcome"].items()
        if tag == "done":
            self.assertEqual(read_outcome(case["json"], Reading()), ScriptDone())
        elif tag == "unknown":
            self.assertEqual(read_outcome(case["json"], Reading()), ScriptUnknown())
        elif tag == "removed":
            self.assertEqual(read_outcome(case["json"], Reading()), ScriptRemoved(body))
        elif tag == "keys":
            got = read_outcome(case["json"], Reading())
            self.assertEqual(
                got.keys,
                tuple(identity_of(k["record"], self.names).split(":", 1)[1] for k in body),
                "each key is the id half alone",
            )
        elif tag == "value":
            ((inner, _),) = body.items()
            reading = Reading() if inner == "none" else Reading(kind_of(body))
            got = read_outcome(case["json"], reading)
            if inner == "none":
                self.assertIs(got.value, NONE, "no value key is the language's none")
            else:
                self.assertTrue(same_value(value_of(body), got.value))
        elif tag == "records":
            self.check_records(case, body)
        else:
            raise AssertionError(f"an outcome this translator does not know: {tag}")

    def check_records(self, case: dict, body: dict) -> None:
        rows = body["rows"]
        reading = Reading(kind_of(rows[0]["value"])) if rows else Reading()
        got = read_outcome(case["json"], reading)
        self.assertEqual(got.path, body["plan"]["access"])
        self.assertEqual(got.plan.access, body["plan"]["access"])
        self.assertEqual(len(got.rows), len(rows))
        for row, expected in zip(got.rows, rows):
            self.assertEqual(
                row.identity, identity_of(expected["id"]["record"], self.names).split(":", 1)[1]
            )
            self.assertTrue(same_value(value_of(expected["value"]), row.value))

        # Exactness: present-and-true, false-with-a-reason, and absent — and the
        # third is not the first.
        if "exact" not in body["plan"]:
            self.assertIsInstance(got.plan.exactness, Unstated)
        elif body["plan"]["exact"]:
            self.assertEqual(got.plan.exactness, Exact())
        else:
            self.assertEqual(got.plan.exactness, Inexact(body["plan"]["inexact"]))

        # Suggestion: absent is not the same claim as present-and-empty.
        if "suggestion" not in body:
            self.assertIsInstance(got.suggestion, NotConsulted)
        elif not body["suggestion"]:
            self.assertEqual(got.suggestion, Complete())
        else:
            self.assertIsInstance(got.suggestion, Corrections)
            self.assertEqual(got.suggestion.items[0].instead, body["suggestion"][0]["instead"])

        self.assertEqual(got.only, body.get("only", False))
        self.assertEqual(len(got.notes), len(body.get("notes", [])))

    def test_a_plan_carries_the_keys_it_knows_and_no_others(self) -> None:
        # A scan's plan is the two keys it knows, not eight of which six say
        # nothing — and `source` and `shape` are open word sets a client renders
        # rather than switches on.
        got = read_outcome(
            {"kind": "records", "path": "scan", "plan": {"access": "scan", "exact": True}, "records": []},
            Reading(),
        )
        self.assertEqual(got.plan.details, {})

        got = read_outcome(
            {
                "kind": "records",
                "path": "index",
                "plan": {"access": "index", "exact": True, "shape": "a shape this build never heard of"},
                "records": [],
            },
            Reading(),
        )
        self.assertEqual(got.plan.details["shape"], "a shape this build never heard of")

    def test_an_unknown_outcome_is_not_an_outcome_carrying_nothing(self) -> None:
        # The difference is the remedy: upgrade, not shrug.
        results = read_results(
            {"results": [{"kind": "done"}, {"kind": "unknown"}, {"kind": "done"}]}, Reading()
        )
        self.assertEqual(
            [type(r) for r in results], [ScriptDone, ScriptUnknown, ScriptDone],
            "a client must not stop reading the list at the first unknown",
        )


if __name__ == "__main__":
    unittest.main()
