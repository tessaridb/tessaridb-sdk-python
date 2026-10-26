"""The container calls' statements, byte for byte, as cache contract §2a writes
them, and — with ``TESSARIDB_TEST_NODE`` set — every call against a running node."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tessaridb import Integer, NotACacheArgument, NULL, Text  # noqa: E402
from tessaridb.cache import _Statements  # noqa: E402
import test_cache  # noqa: E402

USE = "USE NAMESPACE app; USE DATABASE main; "


def texts(*items: str) -> list:
    return [Text(item) for item in items]


class ContainerStatements(unittest.TestCase):
    def test_every_container_statement_is_the_one_the_contract_writes(self) -> None:
        s = _Statements("app", "main", "cache")
        cases = [
            (
                s.list_push("k", texts("a", "b"), False),
                "LIST PUSH cache:$k BACK $v0, $v1;",
                ["k", "v0", "v1"],
            ),
            (
                s.list_push("k", texts("a"), True),
                "LIST PUSH cache:$k FRONT $v0;",
                ["k", "v0"],
            ),
            (s.list_pop("k", True, None), "LIST POP cache:$k FRONT;", ["k"]),
            (s.list_pop("k", False, 3), "LIST POP cache:$k BACK LIMIT $n;", ["k", "n"]),
            (
                s.list_range("k", 0, 10),
                "RETURN array::slice(GET cache:$k, $i, $n);",
                ["k", "i", "n"],
            ),
            (s.list_at("k", -1), "RETURN array::at(GET cache:$k, $i);", ["k", "i"]),
            (s.list_len("k"), "RETURN array::len(GET cache:$k);", ["k"]),
            (
                s.list_remove("k", Text("a"), None),
                "LIST REMOVE cache:$k $v;",
                ["k", "v"],
            ),
            (
                s.list_remove("k", Text("a"), 2),
                "LIST REMOVE cache:$k $v LIMIT $n;",
                ["k", "v", "n"],
            ),
            (
                s.list_trim("k", 0, 99),
                "LIST TRIM cache:$k FROM $i TO $j;",
                ["k", "i", "j"],
            ),
            (
                s.hash_set("k", {"x": Integer(1), "y": NULL}),
                "HASH SET cache:$k $f0 = $v0, $f1 = $v1;",
                ["k", "f0", "v0", "f1", "v1"],
            ),
            (s.hash_get("k", "x"), "RETURN object::get(GET cache:$k, $f);", ["k", "f"]),
            (
                s.hash_remove("k", ["x", "y"]),
                "HASH REMOVE cache:$k $f0, $f1;",
                ["k", "f0", "f1"],
            ),
            (s.hash_incr("k", "x", 2), "HASH INCR cache:$k $f BY $n;", ["k", "f", "n"]),
            (s.hash_len("k"), "RETURN object::len(GET cache:$k);", ["k"]),
            (
                s.members("ADD", "k", texts("a", "b")),
                "MEMBERS ADD cache:$k $v0, $v1;",
                ["k", "v0", "v1"],
            ),
            (
                s.members("REMOVE", "k", texts("a")),
                "MEMBERS REMOVE cache:$k $v0;",
                ["k", "v0"],
            ),
            (s.members_has("k", Text("a")), "RETURN $v IN GET cache:$k;", ["k", "v"]),
            (s.members_len("k"), "RETURN set::len(GET cache:$k);", ["k"]),
            (
                s.scores_add("k", {"ann": 1.0, "bob": 2.5}),
                "SCORES ADD cache:$k $m0 = $c0, $m1 = $c1;",
                ["k", "m0", "c0", "m1", "c1"],
            ),
            (
                s.scores_incr("k", "ann", 1.5),
                "SCORES INCR cache:$k $m BY $c;",
                ["k", "m", "c"],
            ),
            (s.scores_remove("k", ["ann"]), "SCORES REMOVE cache:$k $m0;", ["k", "m0"]),
            (
                s.scores_of("score", "k", "ann"),
                "RETURN scores::score(GET cache:$k, $m);",
                ["k", "m"],
            ),
            (
                s.scores_of("rank", "k", "ann"),
                "RETURN scores::rank(GET cache:$k, $m);",
                ["k", "m"],
            ),
            (
                s.scores_range("k", 0, -1),
                "RETURN scores::range(GET cache:$k, $i, $j);",
                ["k", "i", "j"],
            ),
            (
                s.scores_between("k", 1.0, 2.0),
                "RETURN scores::between(GET cache:$k, $lo, $hi);",
                ["k", "lo", "hi"],
            ),
            (
                s.scores_between("k", None, 2.0),
                "RETURN scores::between(GET cache:$k, NONE, $hi);",
                ["k", "hi"],
            ),
            (
                s.scores_between("k", 1.0, None),
                "RETURN scores::between(GET cache:$k, $lo, NONE);",
                ["k", "lo"],
            ),
        ]
        for (script, given), statement, bound in cases:
            self.assertEqual(script, USE + statement)
            self.assertEqual(list(given), bound, statement)


class ContainersAgainstANode(unittest.TestCase):
    cache = test_cache.CacheAgainstANode.cache

    def test_a_key_holds_a_list_a_hash_a_set_and_a_ranking(self) -> None:
        cache = self.cache()
        self.assertEqual(cache.list_push("l", texts("a", "b", "c", "d")), 4)
        self.assertEqual(cache.list_len("l"), 4)
        self.assertEqual(cache.list_at("l", -1), Text("d"))
        self.assertEqual(cache.list_range("l", 1, 2), texts("b", "c"))
        self.assertEqual(cache.list_pop("l"), Text("a"))
        self.assertEqual(cache.list_pop_many("l", 2, front=False), texts("d", "c"))
        self.assertEqual(cache.list_remove("l", Text("b")), 1)
        self.assertEqual(cache.list_len("l"), 0, "an emptied list is no key")
        self.assertEqual(cache.list_range("nothing", 0, 5), [])
        cache.list_push("t", texts("1", "2", "3"))
        cache.list_trim("t", 0, 1)
        self.assertEqual(cache.list_len("t"), 2)

        self.assertEqual(cache.hash_set("h", {"x": Integer(1), "y": Text("why")}), 2)
        self.assertEqual(cache.hash_get("h", "y"), Text("why"))
        self.assertIsNone(cache.hash_get("h", "z"))
        self.assertEqual(cache.hash_incr("h", "x", 4), 5)
        self.assertEqual(cache.hash_len("h"), 2)
        self.assertEqual(cache.hash_remove("h", ["y"]), 1)
        self.assertEqual(cache.hash_get_all("h"), {"x": Integer(5)})
        self.assertEqual(cache.hash_get_all("nothing"), {})

        self.assertEqual(cache.members_add("s", texts("p", "q")), 2)
        self.assertTrue(cache.members_has("s", Text("p")))
        self.assertFalse(cache.members_has("nothing", Text("p")))
        self.assertEqual(cache.members_len("s"), 2)
        self.assertEqual(cache.members_remove("s", texts("q")), 1)
        self.assertEqual(cache.members_all("s"), texts("p"))

        self.assertEqual(cache.scores_add("r", {"ann": 3, "bob": 1, "cid": 2}), 3)
        self.assertEqual(cache.scores_incr("r", "bob", 0.5), 1.5)
        self.assertEqual(cache.scores_score("r", "ann"), 3.0)
        self.assertEqual(cache.scores_rank("r", "ann"), 2)
        self.assertIsNone(cache.scores_rank("r", "eve"))
        self.assertEqual(cache.scores_range("r", 0, 1), [("bob", 1.5), ("cid", 2.0)])
        self.assertEqual(cache.scores_between("r", low=2), [("cid", 2.0), ("ann", 3.0)])
        self.assertEqual(cache.scores_remove("r", ["ann"]), 1)

        with self.assertRaises(NotACacheArgument):
            cache.list_push("l", [])
        with self.assertRaises(NotACacheArgument):
            cache.list_range("l", -1, 2)


if __name__ == "__main__":
    unittest.main()
