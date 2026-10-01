"""Following a redirect (§3.12), against scripted nodes on loopback.

Each fake listens on its own port, greets, keeps its own session's ``USE``,
answers ``session::context()`` as a node does, and hands every other script to
the test's function — which is how a redirect to a second node is followed
without a cluster.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import unittest
from pathlib import Path
from typing import Callable, Union

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tessaridb import _frames as frames  # noqa: E402
from tessaridb._bytes import Reader, Writer  # noqa: E402
from tessaridb._encode import encode  # noqa: E402
from tessaridb.connection import connect  # noqa: E402
from tessaridb.errors import (  # noqa: E402
    NotFollowable,
    RedirectLoop,
    StaleRedirect,
    WrongNode,
)
from tessaridb.outcome import Records, ValueOutcome  # noqa: E402
from tessaridb.value import Integer, NullValue, Object, Text, Uuid, Value  # noqa: E402

A, B, C = bytes([0xA] * 16), bytes([0xB] * 16), bytes([0xC] * 16)
READ = "SELECT * FROM ledger;"
CONTEXT = "RETURN session::context();"


class Go:
    """A redirect a fake answers with."""

    def __init__(self, node: bytes, epoch: int, settled: bool, endpoint: str) -> None:
        self.node, self.epoch, self.settled, self.endpoint = node, epoch, settled, endpoint


Reply = Union[Value, Go]
Behaviour = Callable[[str], Reply]


class Fake:
    """A node on loopback, and what it was sent."""

    def __init__(self, node: bytes, behave: Behaviour, claims: bytes | None = None) -> None:
        self.node = node
        self.seen: list[str] = []
        self.signed: list[str] = []
        self._behave = behave
        self._claims = claims or node
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.address = "127.0.0.1:%d" % self._listener.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self) -> None:
        self._listener.close()

    def _accept(self) -> None:
        while True:
            try:
                sock, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock: socket.socket) -> None:
        with sock:
            sock.recv(6)
            sock.sendall(b"TESS\x01\x02")
            namespace: Value = NullValue()
            database: Value = NullValue()
            while True:
                body = _request(sock)
                if body is None:
                    return
                r = Reader(body)
                script = r.text("script")
                if r.u8("signed") == 1:
                    self.signed.append(r.text("user"))
                self.seen.append(script)
                reply: Reply
                if script == CONTEXT:
                    reply = Object(
                        {"node": Uuid(self._claims), "namespace": namespace, "database": database}
                    )
                elif script.startswith("USE "):
                    for statement in script.split(";"):
                        words = statement.split()
                        if words[:2] == ["USE", "NAMESPACE"]:
                            namespace = Text(words[2])
                        elif words[:2] == ["USE", "DATABASE"]:
                            database = Text(words[2])
                    reply = NullValue()
                else:
                    reply = self._behave(script)
                if isinstance(reply, Go):
                    w = Writer()
                    w.u64(reply.epoch)
                    w.u8(1 if reply.settled else 2)
                    w.text(reply.endpoint)
                    frames.send(sock, frames.ELSEWHERE, reply.node + w.bytes())
                else:
                    frames.send(sock, frames.ANSWER, _value_answer(reply))


def _exactly(sock: socket.socket, width: int) -> bytes | None:
    out = b""
    while len(out) < width:
        try:
            more = sock.recv(width - len(out))
        except OSError:
            return None
        if not more:
            return None
        out += more
    return out


def _request(sock: socket.socket) -> bytes | None:
    """One Request frame's body — read here, because the client's own reader
    accepts only the kinds a node sends."""
    header = _exactly(sock, 5)
    if header is None or header[0] != frames.REQUEST:
        return None
    return _exactly(sock, int.from_bytes(header[1:], "big"))


def _value_answer(value: Value) -> bytes:
    encoded = encode(value)
    outcome = Writer()
    outcome.u8(2)
    outcome.u32(0)
    outcome.lenbytes(encoded)
    w = Writer()
    w.u32(1)
    w.lenbytes(outcome.bytes())
    return w.bytes()


def answers(n: int) -> Behaviour:
    return lambda _script: Integer(n)


def sends(to: Fake, epoch: int, settled: bool) -> Behaviour:
    """READ goes to ``to``; anything else is answered here."""
    return lambda script: Go(to.node, epoch, settled, to.address) if script == READ else Integer(1)


class FollowingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fakes: list[Fake] = []

    def tearDown(self) -> None:
        for fake in self.fakes:
            fake.close()

    def fake(self, node: bytes, behave: Behaviour, claims: bytes | None = None) -> Fake:
        made = Fake(node, behave, claims)
        self.fakes.append(made)
        return made

    def selected(self, origin: Fake):
        conn = connect(origin.address, "ada", "secret")
        self.addCleanup(conn.close)
        conn.execute("USE NAMESPACE prod; USE DATABASE shop;")
        return conn

    def test_a_transient_redirect_answers_there_and_leaves_the_connection_here(self) -> None:
        b = self.fake(B, answers(42))
        a = self.fake(A, sends(b, 7, False))
        conn = self.selected(a)
        reply = conn.execute(READ)
        self.assertIsNone(reply.redirect)
        answered = reply.outcomes[-1]
        assert isinstance(answered, ValueOutcome)
        self.assertEqual(answered.value, Integer(42))
        self.assertEqual(b.seen, [CONTEXT, "USE NAMESPACE prod; USE DATABASE shop; ", READ])
        self.assertEqual(b.signed[:1], ["ada"], "the credentials were presented there")
        conn.execute("RETURN 1;")
        self.assertEqual(a.seen[-1], "RETURN 1;")
        self.assertEqual(len(b.seen), 3, "B was not asked again")

    def test_a_settled_redirect_moves_the_connection_there(self) -> None:
        b = self.fake(B, answers(42))
        a = self.fake(A, sends(b, 7, True))
        conn = self.selected(a)
        conn.execute(READ)
        conn.execute("RETURN 1;")
        self.assertEqual(b.seen[-1], "RETURN 1;")
        self.assertNotIn("RETURN 1;", a.seen)

    def test_a_node_other_than_the_one_named_is_not_sent_the_request(self) -> None:
        b = self.fake(B, answers(42), claims=C)
        a = self.fake(A, sends(b, 7, False))
        conn = self.selected(a)
        with self.assertRaises(WrongNode) as raised:
            conn.execute(READ)
        self.assertEqual(raised.exception.expected, B)
        self.assertNotIn(READ, b.seen)

    def test_a_redirect_dated_before_one_already_followed_is_refused(self) -> None:
        c = self.fake(C, answers(42))
        b = self.fake(B, sends(c, 3, False))
        a = self.fake(A, sends(b, 5, False))
        conn = self.selected(a)
        with self.assertRaises(StaleRedirect) as raised:
            conn.execute(READ)
        self.assertEqual((raised.exception.epoch, raised.exception.floor), (3, 5))
        self.assertEqual(c.seen, [], "C was never dialled")

    def test_three_hops_and_no_answer_is_a_loop(self) -> None:
        holder: list[str] = []
        c = self.fake(C, lambda _script: Go(C, 1, False, holder[0]))
        holder.append(c.address)
        a = self.fake(A, sends(c, 1, False))
        conn = self.selected(a)
        with self.assertRaises(RedirectLoop) as raised:
            conn.execute(READ)
        self.assertEqual(raised.exception.hops, 3)
        self.assertEqual(c.seen.count(READ), 3, "sent three times and no more")

    def test_a_tenancy_that_is_not_a_plain_name_is_not_followed(self) -> None:
        b = self.fake(B, answers(42))
        a = self.fake(A, sends(b, 7, False))
        conn = connect(a.address)
        self.addCleanup(conn.close)
        conn.execute("USE NAMESPACE pr-od;")
        with self.assertRaises(NotFollowable) as raised:
            conn.execute(READ)
        self.assertEqual(raised.exception.name, "pr-od")
        self.assertEqual(b.seen, [], "B was never dialled")



class LiveClusterTest(unittest.TestCase):
    """A write and a leader-only read sent to a follower of a real two-node
    cluster land on the leader, the read by a transient redirect this client
    follows. ``TESSARIDB_TEST_CLUSTER=<leader host:port>,<follower host:port>``,
    a cluster whose namespace ``prod`` holds database ``shop`` with collection
    ``ledger``."""

    def test_a_misrouted_write_and_read_land_on_the_leader(self) -> None:
        cluster = os.environ.get("TESSARIDB_TEST_CLUSTER")
        if not cluster:
            self.skipTest("TESSARIDB_TEST_CLUSTER is not set")
        leader, follower = cluster.split(",")
        tenancy = "USE NAMESPACE prod; USE DATABASE shop;"
        key = f"python{os.getpid()}"

        def node_of(conn) -> Value:
            answered = conn.execute(CONTEXT).outcomes[-1]
            assert isinstance(answered, ValueOutcome) and isinstance(answered.value, Object)
            return answered.value.fields["node"]

        with connect(follower) as writer:
            # A forward carries the script and not the session.
            writer.execute(f"{tenancy} CREATE ledger:'{key}' = {{ total: 1 }};")
        with connect(leader) as on_leader:
            leader_node = node_of(on_leader)
            on_leader.execute(tenancy)
            found = on_leader.execute(f"SELECT * FROM ledger:'{key}';").outcomes[-1]
            assert isinstance(found, Records)
            self.assertEqual(len(found.rows), 1, "the write landed on the leader")
        with connect(follower) as reader:
            follower_node = node_of(reader)
            self.assertNotEqual(leader_node, follower_node)
            reader.execute(tenancy)
            reply = reader.execute(f"SELECT * FROM ledger:'{key}' ANSWERED BY LEADER;")
            self.assertIsNone(reply.redirect, "the redirect was followed")
            answered = reply.outcomes[-1]
            assert isinstance(answered, Records)
            self.assertEqual(len(answered.rows), 1, "the leader answered")
            self.assertEqual(node_of(reader), follower_node, "a transient redirect stays here")


if __name__ == "__main__":
    unittest.main()
