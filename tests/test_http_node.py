"""The HTTP surface against a running node.

Opt-in like the wire tests, and for the same reason. These prove what no corpus
can: that this client asks the questions the node answers, in the shapes §5 says
it will.

    TESSARIDB_TEST_HTTP=127.0.0.1:47916 \\
    TESSARIDB_TEST_HTTP_CLOSED=127.0.0.1:47918 \\
    TESSARIDB_TEST_USER=corpus TESSARIDB_TEST_PASSWORD=... \\
    python3 -m unittest discover -s tests -t tests
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import tessaridb  # noqa: E402
from tessaridb import (  # noqa: E402
    Healthy,
    HTTPClient,
    HTTPError,
    IntegerKind,
    ObjectKind,
    Reading,
    ScriptRecords,
    ScriptValue,
    TextKind,
)

SCHEMA = (
    "DEFINE NAMESPACE IF NOT EXISTS pyhttp; USE NAMESPACE pyhttp;"
    " DEFINE DATABASE IF NOT EXISTS app; USE DATABASE app;"
    " DEFINE COLLECTION IF NOT EXISTS thing; DEFINE BUCKET IF NOT EXISTS docs;"
)
USE = "USE NAMESPACE pyhttp; USE DATABASE app;"


class OpenStore(unittest.TestCase):
    def setUp(self) -> None:
        address = os.environ.get("TESSARIDB_TEST_HTTP")
        if not address:
            self.skipTest("set TESSARIDB_TEST_HTTP=<host:port> to run the HTTP tests")
        self.node = HTTPClient(address)
        self.node.script(SCHEMA)

    def test_health_and_ready_are_answered_and_are_not_synonyms(self) -> None:
        # They answer identically on a well node and diverge during a staged
        # shutdown, which is the whole reason both exist — so neither is
        # implemented in terms of the other.
        for condition in (self.node.health(), self.node.ready()):
            self.assertIsInstance(condition, Healthy)
            self.assertGreaterEqual(condition.committed, 0)

    def test_a_value_outcome_reads_at_the_kind_it_was_declared_with(self) -> None:
        outcomes = self.node.script(USE + " RETURN 1;", Reading(IntegerKind()))
        self.assertIsInstance(outcomes[-1], ScriptValue)
        self.assertEqual(outcomes[-1].value, tessaridb.Integer(1))

    def test_records_come_back_as_pairs_with_a_plan(self) -> None:
        self.node.script(USE + " DELETE FROM thing WHERE true LIMIT ALL;")
        self.node.script(USE + " CREATE thing:1 = { name: 'ada', n: 42 };")
        outcomes = self.node.script(
            USE + " SELECT * FROM thing:1;",
            Reading(ObjectKind({"name": TextKind(), "n": IntegerKind()})),
        )
        records = outcomes[-1]
        self.assertIsInstance(records, ScriptRecords)
        self.assertEqual(len(records.rows), 1)
        self.assertEqual(records.rows[0].identity, "1", "the id half alone, with no table prefix")
        self.assertEqual(records.rows[0].value.fields["n"], tessaridb.Integer(42))
        self.assertEqual(records.plan.access, records.path)

    def test_a_script_the_parser_refuses_is_a_400_carrying_the_stores_words(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            self.node.script(USE + " SELECT * FROM nosuchtable;")
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("nosuchtable", caught.exception.message)

    def test_a_file_survives_a_path_that_needs_encoding(self) -> None:
        # An unencoded space makes the request LINE unparseable rather than
        # merely wrong, and an unencoded % asks the server to decode an escape
        # the caller never wrote.
        name = "reports/100% done.pdf"
        content = b"one two three"
        self.node.put("pyhttp", "app", "docs", name, content)
        self.assertEqual(self.node.get("pyhttp", "app", "docs", name), content)

        self.node.delete("pyhttp", "app", "docs", name)
        self.assertIsNone(self.node.get("pyhttp", "app", "docs", name))
        # Idempotent: the server reports no difference between removing a file
        # and finding none.
        self.node.delete("pyhttp", "app", "docs", name)

    def test_an_empty_file_is_not_a_missing_one(self) -> None:
        self.node.put("pyhttp", "app", "docs", "empty.txt", b"")
        self.assertEqual(self.node.get("pyhttp", "app", "docs", "empty.txt"), b"")
        self.assertIsNone(self.node.get("pyhttp", "app", "docs", "nothing-here.txt"))
        self.node.delete("pyhttp", "app", "docs", "empty.txt")

    def test_this_node_answers_the_retired_wrapped_listing_Q_PY_007(self) -> None:
        """A known divergence, asserted so it is loud rather than silent.

        `0.0.5-alpha` answers `GET /files/{ns}/{db}/{bucket}` with a whole §5.6
        records outcome wrapped in `files` — the shape §5.1 says is gone,
        carrying the query plan and a storage-level chunk count. Such an element
        still has a `path` key holding the access path, so a reader that trusts
        it returns a file called `scan` and reports success.

        This test fails the day the node is fixed, which is the right direction
        for it to fail in: flip it to the specified shape then. Q-PY-007.
        """
        with self.assertRaises(tessaridb.Malformed) as caught:
            self.node.listing("pyhttp", "app", "docs")
        self.assertIn("Q-PY-007", str(caught.exception))

    def test_this_node_does_not_404_a_name_that_is_not_a_bucket_Q_PY_007(self) -> None:
        """The second half of the same divergence.

        §5.1 and §5.2 both say every `/files` request naming a bucket that is not
        one answers `404`. This node answers `200` for a name declared as a
        COLLECTION — returning that collection's records through the files route
        — and `400` for a name nothing declared. Neither is `404`, and the first
        means the listing route resolves the name as a table rather than a
        bucket.
        """
        with self.assertRaises(tessaridb.Malformed):
            self.node.listing("pyhttp", "app", "thing")
        with self.assertRaises(HTTPError) as caught:
            self.node.listing("pyhttp", "app", "nothingdeclared")
        self.assertEqual(caught.exception.status, 400, "specified as 404")

    def test_this_node_chunks_the_backup_Q_PY_009(self) -> None:
        """A known divergence, asserted so it is loud rather than silent.

        §5.3 requires every response on this surface to declare its length and
        forbids `Transfer-Encoding: chunked` on **any** route, naming
        `GET /backup` as the one whose body has no small upper bound and which
        must still declare it. This node chunks exactly that route, and only that
        route. Q-PY-009.
        """
        import http.client

        host, _, port = os.environ["TESSARIDB_TEST_HTTP"].rpartition(":")
        connection = http.client.HTTPConnection(host, int(port))
        try:
            connection.request("GET", "/backup")
            backup = connection.getresponse()
            self.assertEqual(backup.getheader("Transfer-Encoding"), "chunked", "specified as absent")
            backup.read()
        finally:
            connection.close()

        connection = http.client.HTTPConnection(host, int(port))
        try:
            connection.request("POST", "/script", b"RETURN 1;")
            script = connection.getresponse()
            self.assertIsNone(script.getheader("Transfer-Encoding"), "/script declares its length")
            script.read()
        finally:
            connection.close()

    def test_a_backup_is_the_whole_log_in_one_response(self) -> None:
        # Unauthenticated on a store with no DEFINE USER: the open-store rule at
        # its loudest, not a defect.
        self.assertGreater(len(self.node.backup()), 0)

    def test_an_open_store_has_no_session_to_open_and_is_asked_only_once(self) -> None:
        # `POST /session` answers 401 saying so rather than minting a token: one
        # cut from the ABSENCE of a credential would still work after the first
        # DEFINE USER closed the store, which is precisely what must not happen.
        # The client carries on rather than treating it as fatal, and REMEMBERS —
        # asking per request would cost an Argon2id verification per request and
        # walk straight into this node's sign-in limiter (Q-PY-008).
        node = HTTPClient(os.environ["TESSARIDB_TEST_HTTP"], user="nobody", password="nothing")
        with self.assertRaises(HTTPError) as caught:
            node.script("RETURN 1;", Reading(IntegerKind()))
        self.assertIn(caught.exception.status, (401, 429), "429 is the limiter — Q-PY-008")
        self.assertIsNone(node._token, "no token was minted, and none was needed")
        self.assertTrue(node._sessionless, "asked once, and not again")

        # With no credentials at all, the same open store runs anything.
        outcomes = self.node.script("RETURN 1;", Reading(IntegerKind()))
        self.assertEqual(outcomes[-1].value, tessaridb.Integer(1))

    def test_a_segment_that_is_not_a_name_never_reaches_the_node(self) -> None:
        with self.assertRaises(ValueError):
            self.node.listing("py-http", "app", "docs")


class ClosedStore(unittest.TestCase):
    def setUp(self) -> None:
        address = os.environ.get("TESSARIDB_TEST_HTTP_CLOSED")
        if not address:
            self.skipTest("set TESSARIDB_TEST_HTTP_CLOSED=<host:port> for the session tests")
        self.node = HTTPClient(
            address,
            user=os.environ.get("TESSARIDB_TEST_USER", "corpus"),
            password=os.environ.get("TESSARIDB_TEST_PASSWORD", ""),
        )

    def test_the_password_is_spent_once_and_the_token_serves_after(self) -> None:
        self.node.script("RETURN 1;", Reading(IntegerKind()))
        first = self.node._token
        self.assertIsNotNone(first, "a closed store mints a token")
        self.assertEqual(len(first), 64, "64 hexadecimal characters, always exactly 64")
        bytes.fromhex(first)
        self.node.script("RETURN 2;", Reading(IntegerKind()))
        self.assertEqual(self.node._token, first, "the same token, not a second sign-in")

    def test_a_token_given_back_is_re_opened_on_the_next_call(self) -> None:
        # DELETE /session is one of the four ways a token ends, and a client
        # cannot tell them apart: discard, sign in again, retry once.
        self.node.script("RETURN 1;", Reading(IntegerKind()))
        first = self.node._token
        self.node.end_session()
        self.assertIsNone(self.node._token)
        self.node.script("RETURN 1;", Reading(IntegerKind()))
        self.assertIsNotNone(self.node._token)
        self.assertNotEqual(self.node._token, first)

    def test_a_stale_token_is_retried_once_and_the_caller_never_sees_it(self) -> None:
        # The four ways a token ends all answer 401, so this forges the fifth:
        # a token the node never issued. The retry is what the caller relies on.
        self.node.script("RETURN 1;", Reading(IntegerKind()))
        self.node._token = "0" * 64
        outcomes = self.node.script("RETURN 7;", Reading(IntegerKind()))
        self.assertEqual(outcomes[-1].value, tessaridb.Integer(7))
        self.assertNotEqual(self.node._token, "0" * 64, "a fresh token replaced the dead one")

    def test_a_name_no_user_holds_is_a_401(self) -> None:
        # Deliberately a name nobody holds rather than the real user with a wrong
        # password: this node's sign-in limiter is per user, and a test that
        # burned attempts on `corpus` would lock out the account every other
        # test here depends on. Q-PY-008.
        node = HTTPClient(
            os.environ["TESSARIDB_TEST_HTTP_CLOSED"], user="nosuchuser", password="nothing"
        )
        with self.assertRaises(HTTPError) as caught:
            node.script("RETURN 1;")
        self.assertIn(caught.exception.status, (401, 429))

    def test_this_node_answers_429_to_a_repeated_sign_in_Q_PY_008(self) -> None:
        """A known divergence, asserted so it is loud rather than silent.

        §5.2 enumerates sixteen refusal behaviours and `429` is not among them.
        This node applies a per-user sign-in limiter that answers
        `429 {"error":"this node is not taking a sign-in for that user right
        now"}` — and it then refuses the CORRECT password for that user, so a
        client following §5.8's "discard, sign in again, retry once" can drive a
        valid account into a lockout. This client therefore retries a `401` once
        and never a `429`.

        The user here is one nobody holds, so the lockout lands nowhere. Q-PY-008.
        """
        who = f"locked{os.getpid()}"
        seen = set()
        for _ in range(4):
            node = HTTPClient(os.environ["TESSARIDB_TEST_HTTP_CLOSED"], user=who, password="x")
            try:
                node.script("RETURN 1;")
            except HTTPError as why:
                seen.add(why.status)
        self.assertIn(429, seen, "the limiter is real and is not in §5.2")
        self.assertFalse(seen - {401, 429}, f"unexpected statuses {seen}")


if __name__ == "__main__":
    unittest.main()
