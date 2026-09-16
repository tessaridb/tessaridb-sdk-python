"""Exercised against a running node.

Opt-in, because a suite that needs a server cannot be the suite that runs on a
clean checkout. These are also the only tests here that prove SEMANTICS:
everything else proves this client agrees with the specification's bytes, and a
client can agree with the bytes and still ask the wrong question.

    TESSARIDB_TEST_NODE=127.0.0.1:47915 python3 -m unittest discover -s tests -t tests

The closed-store tests additionally need a node whose store has a user declared,
because a session is the only thing that makes credentials mean anything:

    TESSARIDB_TEST_CLOSED=127.0.0.1:47917 \\
    TESSARIDB_TEST_USER=corpus TESSARIDB_TEST_PASSWORD=... python3 -m unittest ...

The suite seeds its own fixture and owns every record it asserts on.
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import tessaridb  # noqa: E402
from tessaridb import Bool, Integer, Object, Records, Refused, Text, ValueOutcome  # noqa: E402

SCHEMA = """
DEFINE NAMESPACE IF NOT EXISTS pycorpus;
USE NAMESPACE pycorpus;
DEFINE DATABASE IF NOT EXISTS app;
USE DATABASE app;
DEFINE COLLECTION IF NOT EXISTS thing;
"""
USE = "USE NAMESPACE pycorpus; USE DATABASE app;"


def node(test: unittest.TestCase) -> tessaridb.Connection:
    address = os.environ.get("TESSARIDB_TEST_NODE")
    if not address:
        test.skipTest("set TESSARIDB_TEST_NODE=<host:port> to run the live tests")
    conn = tessaridb.connect(address)
    test.addCleanup(conn.close)
    return conn


def seed(conn: tessaridb.Connection) -> None:
    conn.execute(SCHEMA)
    # Emptied first: these tests select by predicate as well as by identity, so
    # the collection holds what this function put there and nothing else. A
    # delete over a set must state its ceiling.
    conn.execute(USE + " DELETE FROM thing WHERE true LIMIT ALL;")
    conn.execute(
        USE
        + """ CREATE thing:1 = { name: 'alice', n: 42,
            at: datetime '2026-09-17T00:00:00Z',
            spot: geometry { type: 'Point', coordinates: [2.3522, 48.8566] } };"""
    )


class Live(unittest.TestCase):
    def test_a_greeting_is_exchanged_and_a_select_keeps_its_types(self) -> None:
        conn = node(self)
        seed(conn)

        outcome = conn.execute(USE + " SELECT * FROM thing:1;").outcomes[-1]
        self.assertIsInstance(outcome, Records)
        self.assertEqual(len(outcome.rows), 1, "the collection holds what the fixture wrote")

        value = outcome.rows[0].value
        self.assertIsInstance(value, Object)
        # The whole point of the wire protocol: these come back as themselves
        # rather than narrowed into JSON's six types.
        self.assertEqual(value.fields["n"], Integer(42))
        self.assertEqual(type(value.fields["at"]).__name__, "Datetime")
        point = value.fields["spot"].shape
        # Longitude first, as RFC 7946 fixes. The opposite order is silent: a
        # point in Paris becomes a point in the Indian Ocean, which is a
        # perfectly valid place.
        self.assertAlmostEqual(point.position.lon, 2.3522, places=4)
        self.assertAlmostEqual(point.position.lat, 48.8566, places=4)

    def test_a_parameter_is_bound_as_a_value_and_never_formatted_into_the_script(self) -> None:
        conn = node(self)
        seed(conn)

        hostile = "'; DROP COLLECTION thing; --"
        outcome = conn.execute(USE + " RETURN $x;", {"x": Text(hostile)}).outcomes[-1]
        self.assertIsInstance(outcome, ValueOutcome)
        self.assertEqual(outcome.value, Text(hostile))
        # And the collection it named is still there.
        conn.execute(USE + " SELECT * FROM thing:1;")

    def test_a_value_outcome_carries_a_length_before_its_value(self) -> None:
        # The bug this test exists for shipped in a sibling client: §3.5 writes
        # the outcome as "names · `bytes` value", and a client that reads the
        # value raw reads the length's first byte as a type tag. It is loud when
        # it happens — and only for a client that ever asks for a value outcome,
        # which a suite that only SELECTs never does.
        conn = node(self)
        seed(conn)
        for script, expected in (
            ("RETURN 1;", Integer(1)),
            ("RETURN 'hello';", Text("hello")),
            ("RETURN true;", Bool(True)),
            ("RETURN NONE;", tessaridb.NONE),
        ):
            with self.subTest(script):
                outcome = conn.execute(USE + " " + script).outcomes[-1]
                self.assertIsInstance(outcome, ValueOutcome)
                self.assertEqual(outcome.value, expected)

    def test_a_refusal_carries_the_stores_own_words_and_leaves_the_connection_open(self) -> None:
        conn = node(self)
        seed(conn)
        with self.assertRaises(Refused) as caught:
            conn.execute(USE + " SELECT * FROM nosuchtable;")
        self.assertIn("nosuchtable", caught.exception.message)
        # A refusal is an answer, not a fault: a client that mistyped a statement
        # has not stopped being a client.
        conn.execute(USE + " SELECT * FROM thing:1;")

    def test_a_connection_holds_one_session_across_statements(self) -> None:
        conn = node(self)
        conn.execute(SCHEMA)
        # The USE in one statement is still in force in the next, which is what
        # makes this a session rather than a sequence of independent requests.
        conn.execute(USE)
        conn.execute("SELECT * FROM thing LIMIT 1;")

    def test_a_subscription_delivers_a_change_written_by_another_connection(self) -> None:
        address = os.environ.get("TESSARIDB_TEST_NODE")
        if not address:
            self.skipTest("set TESSARIDB_TEST_NODE=<host:port> to run the live tests")

        setup = node(self)
        seed(setup)

        watcher = tessaridb.connect(address)
        self.addCleanup(watcher.close)
        watcher.execute(USE)
        changes = watcher.subscribe(from_=0)

        # A unique identity per run, because a subscription from the start
        # replays history and the first change delivered is somebody else's.
        identity = f"watched-{time.time_ns()}"
        setup.execute(USE + f" CREATE thing:'{identity}' = {{ seen: true }};")

        watcher._sock.settimeout(10)
        for change in changes:
            if change.identity != identity:
                continue
            self.assertFalse(change.removed, "the record was written, not removed")
            self.assertEqual(change.table, "thing")
            self.assertEqual(change.value.fields["seen"], Bool(True))
            # The +1 is this client's to own: resuming at a position already
            # handled delivers it twice, and both mistakes are silent.
            self.assertEqual(changes.resume_from, change.sequence + 1)
            return
        self.fail("the change never arrived")

    def test_a_subscription_no_longer_answers_statements(self) -> None:
        # §3.10: subscribing consumes the connection. An API that hid this would
        # be promising a multiplexing the protocol does not perform.
        conn = node(self)
        conn.execute(USE)
        conn.subscribe(from_=0)
        with self.assertRaises(tessaridb.TessariError):
            conn.execute("SELECT 1;")


class ClosedStore(unittest.TestCase):
    """A store with a user declared, where credentials mean something."""

    def setUp(self) -> None:
        self.address = os.environ.get("TESSARIDB_TEST_CLOSED")
        if not self.address:
            self.skipTest("set TESSARIDB_TEST_CLOSED=<host:port> to run the closed-store tests")
        self.user = os.environ.get("TESSARIDB_TEST_USER", "corpus")
        self.password = os.environ.get("TESSARIDB_TEST_PASSWORD", "")

    def test_an_unauthenticated_connection_is_refused_by_the_session(self) -> None:
        # A closed store's refusal comes from the session, not from a second rule
        # in this client — which is what keeps an empty store usable.
        conn = tessaridb.connect(self.address)
        self.addCleanup(conn.close)
        with self.assertRaises(Refused):
            conn.execute("RETURN 1;")

    def test_credentials_are_spent_once_and_the_session_holds_after(self) -> None:
        # Measured rather than assumed. The store verifies a password with
        # Argon2id at the OWASP floor, so presenting one per statement pays that
        # cost per statement; §3.10 says the session is the connection, and this
        # is the test that the node agrees.
        conn = tessaridb.connect(self.address, user=self.user, password=self.password)
        self.addCleanup(conn.close)
        conn.execute("DEFINE NAMESPACE IF NOT EXISTS pycorpus;")
        self.assertFalse(conn._owed, "the credentials were presented on the first request")
        conn.execute("USE NAMESPACE pycorpus; DEFINE DATABASE IF NOT EXISTS app;")

    def test_a_wrong_password_is_refused_rather_than_ignored(self) -> None:
        conn = tessaridb.connect(self.address, user=self.user, password=self.password + "-wrong")
        self.addCleanup(conn.close)
        with self.assertRaises(Refused):
            conn.execute("RETURN 1;")


if __name__ == "__main__":
    unittest.main()
