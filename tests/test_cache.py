"""The cache handle's statements, byte for byte, as cache contract §2 writes them,
and — with ``TESSARIDB_TEST_NODE`` set — every call against a running node (§6)."""

import os
import sys
import time
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import tessaridb  # noqa: E402
from tessaridb import (
    NONE,
    NULL,
    BuilderError,
    Cache,
    Datetime,
    Duration,
    Integer,
    NotACacheArgument,
    Text,
)  # noqa: E402
from tessaridb.cache import _duration, _Statements, unquoted  # noqa: E402

USE = "USE NAMESPACE app; USE DATABASE main; "


class Statements(unittest.TestCase):
    def test_every_statement_is_the_one_the_contract_writes(self) -> None:
        s = _Statements("app", "main", "cache")
        ttl = Duration(30, 0)
        cases = [
            (s.get("k"), "GET cache:$k;", ["k"]),
            (s.set("k", NULL), "SET cache:$k = $v;", ["k", "v"]),
            (
                s.set("k", NULL, ttl=ttl),
                "SET cache:$k = $v EXPIRE $t;",
                ["k", "v", "t"],
            ),
            (
                s.set("k", NULL, " IF ABSENT", ttl=ttl),
                "SET cache:$k = $v IF ABSENT EXPIRE $t;",
                ["k", "v", "t"],
            ),
            (
                s.set("k", NULL, " IF PRESENT"),
                "SET cache:$k = $v IF PRESENT;",
                ["k", "v"],
            ),
            (
                s.set("k", NULL, " IF = $e", NULL),
                "SET cache:$k = $v IF = $e;",
                ["k", "v", "e"],
            ),
            (s.delete("k"), "DELETE cache:$k RETURN BEFORE;", ["k"]),
            (s.incr("k", 5), "INCR cache:$k BY $n;", ["k", "n"]),
            (s.ttl("k"), "RETURN TTL cache:$k;", ["k"]),
            (s.expire("k", ttl), "EXPIRE cache:$k $t;", ["k", "t"]),
            (s.persist("k"), "PERSIST cache:$k;", ["k"]),
            (s.keys(None, None, 100), "KEYS FROM cache LIMIT 100;", []),
            (s.keys("", None, 100), "KEYS FROM cache LIMIT 100;", []),
            (
                s.keys("user:", "user:1", 10),
                "KEYS FROM cache PREFIX $p AFTER $a LIMIT 10;",
                ["p", "a"],
            ),
            (
                s.lock("k", "w1", ttl),
                "SET cache:$k = $h IF ABSENT EXPIRE $t;",
                ["k", "h", "t"],
            ),
            (
                s.extend("k", "w1", ttl),
                "SET cache:$k = $h IF = $h EXPIRE $t;",
                ["k", "h", "t"],
            ),
            (
                s.release("k", "w1"),
                "SET cache:$k = 'free' IF = $h EXPIRE 1ms;",
                ["k", "h"],
            ),
        ]
        for (script, given), statement, bound in cases:
            self.assertEqual(script, USE + statement)
            self.assertEqual(list(given), bound, statement)

    def test_a_name_that_is_not_one_is_refused(self) -> None:
        with self.assertRaises(BuilderError):
            _Statements("app", "main", "ca-che")

    def test_a_ttl_is_positive_and_exact(self) -> None:
        self.assertEqual(_duration(1.5), Duration(1, 500_000_000))
        self.assertEqual(_duration(timedelta(minutes=2)), Duration(120, 0))
        for bad in (0, -1, timedelta(0)):
            with self.assertRaises(NotACacheArgument):
                _duration(bad)

    def test_a_quoted_key_is_the_string_again(self) -> None:
        self.assertEqual(unquoted("'user:1'"), "user:1")
        self.assertEqual(unquoted(r"'it\'s'"), "it's")
        self.assertEqual(unquoted(r"'a\\b'"), "a\\b")
        self.assertEqual(unquoted("42"), "42")


def node(test: unittest.TestCase) -> str:
    address = os.environ.get("TESSARIDB_TEST_NODE")
    if not address:
        test.skipTest("set TESSARIDB_TEST_NODE=<host:port> to run the live tests")
    return address


class CacheAgainstANode(unittest.TestCase):
    def cache(self) -> Cache:
        address = node(self)
        conn = tessaridb.connect(address)
        self.addCleanup(conn.close)
        space = f"cache_{time.time_ns()}"
        conn.execute(
            "DEFINE NAMESPACE IF NOT EXISTS pycache; USE NAMESPACE pycache; "
            f"DEFINE DATABASE IF NOT EXISTS app; USE DATABASE app; DEFINE SPACE {space};"
        )
        return Cache(conn, "pycache", "app", space)

    def test_a_value_is_stored_read_counted_expired_and_deleted(self) -> None:
        cache = self.cache()
        at = Datetime(1_790_000_000, 5)
        cache.set("user:42", at, ttl=30)
        self.assertEqual(cache.get("user:42"), at)
        self.assertIsInstance(cache.ttl("user:42"), Duration)
        cache.set("user:42", Integer(1))
        self.assertEqual(cache.ttl("user:42"), NULL, "a plain set kept the expiry")
        self.assertTrue(cache.expire("user:42", 60))
        self.assertTrue(cache.persist("user:42"))
        self.assertEqual(cache.ttl("nobody"), NONE)
        self.assertEqual(cache.incr("hits", 5), 5)
        self.assertEqual(cache.incr("hits"), 6)
        self.assertTrue(cache.set_if_absent("once", Integer(1)))
        self.assertFalse(cache.set_if_absent("once", Integer(2)))
        self.assertFalse(cache.set_if_present("never", Integer(1)))
        self.assertFalse(cache.compare_and_set("once", Integer(9), Integer(3)))
        self.assertTrue(cache.compare_and_set("once", Integer(1), Integer(3)))
        cache.set("it's\\here", NULL)
        self.assertEqual(cache.keys(prefix="it"), ["it's\\here"])
        self.assertEqual(cache.keys(after="it's\\here", limit=1), ["once"])
        self.assertTrue(cache.delete("it's\\here"), "a key holding NULL is a key")
        self.assertFalse(cache.delete("it's\\here"))
        self.assertIsNone(cache.get("it's\\here"))
        with self.assertRaises(NotACacheArgument):
            cache.keys(limit=0)

    def test_get_or_set_loads_once(self) -> None:
        cache = self.cache()
        first = cache.get_or_set("page", 30, lambda: Text("rendered"))
        second = cache.get_or_set("page", 30, lambda: Text("again"))
        self.assertEqual((first, second), (Text("rendered"), Text("rendered")))
        self.assertIsInstance(cache.ttl("page"), Duration, "stored without its ttl")

    def test_a_lease_is_extended_by_its_holder_and_released_so_the_next_can_take_it(
        self,
    ) -> None:
        cache = self.cache()
        lease = cache.lock("report", 30)
        assert lease is not None
        self.assertEqual(len(lease.holder), 32)
        self.assertIsNone(
            cache.lock("report", 30, holder="other"), "a held lock was taken"
        )
        self.assertTrue(lease.extend(), "its holder could not extend it")
        stranger = tessaridb.Lease(lease.key, "other", lease.ttl, cache)
        self.assertFalse(stranger.release(), "another holder released it")
        self.assertTrue(lease.release())
        time.sleep(0.02)
        self.assertIsNotNone(
            cache.lock("report", 30, holder="next"),
            "a released lock could not be taken again, so the release left it permanent",
        )
        self.assertEqual(cache.get("report"), Text("next"))


if __name__ == "__main__":
    unittest.main()
