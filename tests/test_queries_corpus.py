"""The query corpus — every rendering byte-identical, every refusal refused, and
then the rendered ones executed by a node.

The corpus and the contract together make every language's builder agree with
every other. Neither makes a builder agree with the **parser**, which lives in
one repository no client may link — so the last test here sends each rendered
statement to a running node with its parameters bound, which is the only check
that reaches it.

The contract's own provenance is worth carrying: version 1 was written by reading
the rendering an existing builder emitted, so it describes a rendering rather than
having dictated one. What passing this proves is that two implementations of the
document agree and that a node accepts the result — not that this rendering is
the one anybody would have chosen.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from corpus import read_corpus, same_value, value_of  # noqa: E402

import tessaridb  # noqa: E402
from tessaridb import BuilderError, compare, create, delete, select, update  # noqa: E402

CASES_WHEN_WRITTEN = 30

OPERATORS = {"eq": "=", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}


def filter_of(node: dict):
    if "compare" in node:
        it = node["compare"]
        return compare(it["field"], OPERATORS[it["op"]], value_of(it["value"]))
    if "and" in node:
        left, right = node["and"]
        return filter_of(left) & filter_of(right)
    if "or" in node:
        left, right = node["or"]
        return filter_of(left) | filter_of(right)
    raise AssertionError(f"the corpus carries a filter this translator does not know: {node}")


def build(node: dict):
    """The corpus's own description of a query, turned into calls on this builder."""
    if "select" in node:
        it = node["select"]
        query = select(it["from"])
        for item in it.get("fields", []):
            if isinstance(item, str):
                query.field(item)
            else:
                window = item["lines"]
                query.lines(window["field"], window["start"], window["count"])
        if "where" in it:
            query.where(filter_of(it["where"]))
        for field, direction in it.get("order", []):
            query.order_by(field, descending=direction == "desc")
        if "start" in it:
            query.start(it["start"])
        if "limit" in it:
            query.limit(it["limit"])
        return query
    if "create_record" in node:
        it = node["create_record"]
        return create(it["table"], value_of(it["id"])).set(
            {name: value_of(v) for name, v in it["set"].items()}
        )
    if "create_in_table" in node:
        it = node["create_in_table"]
        return create(it["table"]).set({name: value_of(v) for name, v in it["set"].items()})
    if "update_record" in node:
        it = node["update_record"]
        return update(it["table"], value_of(it["id"])).set(
            {name: value_of(v) for name, v in it["set"].items()}
        )
    if "delete_record" in node:
        it = node["delete_record"]
        return delete(it["table"], value_of(it["id"]))
    raise AssertionError(f"the corpus carries a statement this translator does not know: {node}")


class QueryCorpus(unittest.TestCase):
    def setUp(self) -> None:
        self.corpus = read_corpus("queries-v1.json")
        self.assertGreaterEqual(
            len(self.corpus["cases"]),
            CASES_WHEN_WRITTEN,
            "the corpus shrank — a suite cannot get stronger by losing cases",
        )

    def test_every_rendering_is_byte_identical_including_its_numbering(self) -> None:
        # The numbering matters as much as the text: the same query built in any
        # client language has to be the same statement, and a parameter that
        # renders as $p1 here and $p0 elsewhere is a different one.
        for case in self.corpus["cases"]:
            if "refused" in case:
                continue
            with self.subTest(case["name"]):
                rendered = build(case["build"]).render()
                self.assertEqual(rendered.script, case["script"])
                self.assertEqual(
                    sorted(rendered.parameters), sorted(case["parameters"]),
                    "the parameter names differ, so the numbering does",
                )
                for name, expected in case["parameters"].items():
                    self.assertTrue(
                        same_value(value_of(expected), rendered.parameters[name]),
                        f"{name} bound {rendered.parameters[name]!r}",
                    )

    def test_every_refusal_is_refused_with_its_stated_reason_and_never_rendered(self) -> None:
        # Refused rather than quoted into acceptance. There are exactly two
        # reasons and this builder never invents a third.
        for case in self.corpus["cases"]:
            if "refused" not in case:
                continue
            with self.subTest(case["name"]):
                refused = case["refused"]
                with self.assertRaises(BuilderError) as caught:
                    build(case["build"]).render()
                self.assertEqual(caught.exception.reason, refused["reason"])
                if "what" in refused:
                    self.assertEqual(caught.exception.what, refused["what"])
                    self.assertEqual(caught.exception.name, refused["name"])

    def test_the_corpus_carries_both_refusal_reasons_and_no_third(self) -> None:
        reasons = {c["refused"]["reason"] for c in self.corpus["cases"] if "refused" in c}
        self.assertEqual(reasons, {"not-a-name", "incomplete"})


class AgainstTheParser(unittest.TestCase):
    """The only check that reaches the parser no client may link."""

    def setUp(self) -> None:
        address = os.environ.get("TESSARIDB_TEST_NODE")
        if not address:
            self.skipTest("set TESSARIDB_TEST_NODE=<host:port> to run the builder against a node")
        self.conn = tessaridb.connect(address)
        self.addCleanup(self.conn.close)
        self.conn.execute(
            "DEFINE NAMESPACE IF NOT EXISTS pyqueries; USE NAMESPACE pyqueries;"
            " DEFINE DATABASE IF NOT EXISTS app; USE DATABASE app;"
            " DEFINE COLLECTION IF NOT EXISTS memories;"
        )

    def test_a_node_accepts_and_runs_every_rendered_case(self) -> None:
        corpus = read_corpus("queries-v1.json")
        ran = 0
        for case in corpus["cases"]:
            if "refused" in case:
                continue
            with self.subTest(case["name"]):
                rendered = build(case["build"]).render()
                # Each case owns the collection it runs against: emptied, and
                # seeded only when the statement names a record that must exist.
                self.conn.execute("DELETE FROM memories WHERE true LIMIT ALL;")
                if rendered.script.startswith(("UPDATE", "DELETE")):
                    self.conn.execute("CREATE memories:'note-1' = { body: 'seed' };")
                self.conn.execute(rendered.script, rendered.parameters)
                ran += 1
        self.assertEqual(ran, len(corpus["cases"]) - 9, "every rendered case reached the node")


if __name__ == "__main__":
    unittest.main()
