"""The queue corpus — every statement a worker sends, byte-identical, and every
name or count it must refuse, refused before anything is sent.

Rendered by a second implementation of ``spec/queue-v1.md``; the class checked
here is the one the worker builds every statement through.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from corpus import read_corpus  # noqa: E402

from tessaridb import BuilderError  # noqa: E402
from tessaridb.query import Filter, Operator, compare  # noqa: E402
from tessaridb.queue import _Statements  # noqa: E402
from tessaridb.value import Integer, Text, Value  # noqa: E402


def value(json: dict) -> Value:
    ((kind, held),) = json.items()
    return {"string": lambda: Text(held), "integer": lambda: Integer(int(held))}[kind]()


def encoded(held: Value) -> dict:
    if isinstance(held, Text):
        return {"string": held.value}
    if isinstance(held, Integer):
        return {"integer": str(held.value)}
    raise AssertionError(f"a value this test does not encode: {held!r}")


def where(node: dict) -> Filter:
    if "compare" in node:
        comparison = node["compare"]
        return compare(comparison["field"], Operator[comparison["op"].upper()], value(comparison["value"]))
    ((joiner, (left, right)),) = node.items()
    return where(left) & where(right) if joiner == "and" else where(left) | where(right)


def rendered(kind: str, fields: dict) -> tuple[str, dict]:
    statements = _Statements(fields["namespace"], fields["database"], fields["queue"], fields.get("worker", "worker"))
    if kind == "claim":
        narrowed = where(fields["where"]) if "where" in fields else None
        script, parameters = statements.claim(fields["count"], narrowed)
    elif kind == "done":
        script, parameters = statements.done(fields["id"]), {}
    elif kind == "release":
        after = fields["after_ms"] / 1000 if "after_ms" in fields else None
        script, parameters = statements.release(fields["id"], after), {}
    else:
        raise AssertionError(f"a build kind this test does not know: {kind}")
    return script, {name: encoded(held) for name, held in parameters.items()}


class QueueCorpus(unittest.TestCase):
    def test_every_queue_statement_renders_as_the_corpus_says(self) -> None:
        cases = read_corpus("queue-v1.json")["cases"]
        self.assertTrue(cases, "a corpus with no cases checks nothing")
        for case in cases:
            with self.subTest(case["name"]):
                ((kind, fields),) = case["build"].items()
                if "refused" in case:
                    with self.assertRaises(BuilderError) as raised:
                        rendered(kind, fields)
                    error = raised.exception
                    self.assertEqual(
                        {"reason": error.reason, "what": error.what, "name": error.name}, case["refused"]
                    )
                else:
                    script, parameters = rendered(kind, fields)
                    self.assertEqual(script, case["script"])
                    self.assertEqual(parameters, case["parameters"])


if __name__ == "__main__":
    unittest.main()
