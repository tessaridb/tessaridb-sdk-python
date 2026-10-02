"""TLS to a node (protocol §1.1).

The unit half needs nothing. The live half needs a node started with a
certificate, and the PEM of the authority that issued it:

    TESSARIDB_TEST_TLS_NODE=127.0.0.1:47919 TESSARIDB_TEST_TLS_HTTP=127.0.0.1:47920 \\
    TESSARIDB_TEST_TLS_AUTHORITY=/path/to/ca.pem python3 -m unittest discover -s tests -t tests
"""

from __future__ import annotations

import http.client
import os
import ssl
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import tessaridb  # noqa: E402
from tessaridb import Healthy, Integer, ValueOutcome  # noqa: E402
from tessaridb.tls import host_of  # noqa: E402


class Unit(unittest.TestCase):
    def test_the_name_checked_is_the_host_without_its_port(self) -> None:
        self.assertEqual(host_of("db.example:9080"), "db.example")
        self.assertEqual(host_of("[::1]:9080"), "::1")
        self.assertEqual(host_of("127.0.0.1:9080"), "127.0.0.1")

    def test_a_context_that_does_not_verify_is_refused(self) -> None:
        unchecked = ssl.create_default_context()
        unchecked.check_hostname = False
        unchecked.verify_mode = ssl.CERT_NONE
        with self.assertRaises(ValueError):
            tessaridb.connect("127.0.0.1:1", tls=unchecked)
        with self.assertRaises(ValueError):
            tessaridb.HTTPClient("127.0.0.1:1", tls=unchecked)

    def test_a_context_asks_for_tls_1_3_and_checks_the_name(self) -> None:
        context = tessaridb.tls_context()
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_3)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)


class Live(unittest.TestCase):
    def setUp(self) -> None:
        self.wire = os.environ.get("TESSARIDB_TEST_TLS_NODE")
        self.http = os.environ.get("TESSARIDB_TEST_TLS_HTTP")
        self.authority = os.environ.get("TESSARIDB_TEST_TLS_AUTHORITY")
        if not (self.wire and self.http and self.authority):
            self.skipTest(
                "set TESSARIDB_TEST_TLS_NODE, _HTTP and _AUTHORITY to run the TLS tests"
            )

    def test_a_client_that_verified_the_node_is_answered_on_the_wire_and_over_http(
        self,
    ) -> None:
        context = tessaridb.tls_context(self.authority)
        with tessaridb.connect(self.wire, tls=context) as conn:
            reply = conn.execute("RETURN 40 + 2;")
        self.assertEqual(len(reply.outcomes), 1)
        outcome = reply.outcomes[0]
        self.assertIsInstance(outcome, ValueOutcome)
        self.assertEqual(outcome.value, Integer(42))
        health = tessaridb.HTTPClient(self.http, tls=context).health()
        self.assertIsInstance(health, Healthy)

    def test_a_client_in_the_clear_is_not_answered(self) -> None:
        with self.assertRaises(
            (tessaridb.NotThisProtocol, tessaridb.IoError, tessaridb.Truncated)
        ):
            tessaridb.connect(self.wire)
        # A TLS alert where a status line belongs: no HTTP answer at all.
        with self.assertRaises((OSError, http.client.HTTPException)):
            tessaridb.HTTPClient(self.http).health()

    def test_a_client_trusting_another_authority_refuses_the_node(self) -> None:
        # The platform's store cannot hold an authority minted for this run.
        with self.assertRaises(tessaridb.TlsError):
            tessaridb.connect(self.wire, tls=tessaridb.tls_context())
        with self.assertRaises(tessaridb.TlsError):
            tessaridb.HTTPClient(self.http, tls=tessaridb.tls_context()).health()


if __name__ == "__main__":
    unittest.main()
