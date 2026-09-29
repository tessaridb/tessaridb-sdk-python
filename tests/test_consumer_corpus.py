"""The consumer corpus — every statement a consumer sends, byte-identical, and every
name it must refuse, refused before anything is sent.

Each client's live consumer test proves only that its own node accepts what it
sends. This corpus is what makes the five clients agree with one another: it is
rendered by a second implementation of ``spec/consumer-v1.md``, and the class it
checks here is the one the consumer builds every statement through.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from corpus import read_corpus  # noqa: E402

from tessaridb import BuilderError  # noqa: E402
from tessaridb.consumer import _Statements  # noqa: E402
from tessaridb.value import Integer  # noqa: E402


def rendered(kind: str, fields: dict, statements: _Statements) -> tuple[str, dict]:
    positions = tuple(fields.get("positions", ()))
    if kind == "read":
        script, parameters = statements.read(fields["limit"]), {}
    elif kind == "ack":
        script, parameters = statements.ack(positions)
    elif kind == "nack":
        delay = fields["delay_ms"] / 1000 if "delay_ms" in fields else None
        script, parameters = statements.nack(positions, delay)
    else:
        raise AssertionError(f"a build kind this test does not know: {kind}")
    bound = {}
    for name, value in parameters.items():
        assert isinstance(value, Integer), f"{name} is bound as an integer"
        bound[name] = {"integer": str(value.value)}
    return script, bound


class ConsumerCorpus(unittest.TestCase):
    def test_every_consumer_statement_renders_as_the_corpus_says(self) -> None:
        cases = read_corpus("consumer-v1.json")["cases"]
        self.assertTrue(cases, "a corpus with no cases checks nothing")
        for case in cases:
            with self.subTest(case["name"]):
                ((kind, fields),) = case["build"].items()
                where = (fields["namespace"], fields["database"], fields["topic"], fields["group"])
                if "refused" in case:
                    with self.assertRaises(BuilderError) as raised:
                        _Statements(*where)
                    error = raised.exception
                    self.assertEqual(
                        {"reason": error.reason, "what": error.what, "name": error.name}, case["refused"]
                    )
                else:
                    script, parameters = rendered(kind, fields, _Statements(*where))
                    self.assertEqual(script, case["script"])
                    self.assertEqual(parameters, case["parameters"])


if __name__ == "__main__":
    unittest.main()
