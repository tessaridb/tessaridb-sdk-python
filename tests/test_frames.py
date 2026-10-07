"""The frame layer and the answer body, where a node cannot easily be asked.

These are written against the specification's bytes rather than against a running
node, and that is a weaker thing — the live tests beside them are the ones that
prove this client agrees with a store. What belongs here is what a node in a
healthy state will not produce on demand: a peer that is not a node, a frame
above the ceiling, an outcome tag from a future build, and the three fields at
the end of a Records body that an older node simply omits.

Each of those is a rule the specification states as a MUST and whose failure is
silent. An unread appended field reads as a default; an unskipped unknown outcome
mis-parses every outcome after it; an exactness absent read as exact puts a
promise into the mouth of a node that made none.
"""

from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tessaridb import NONE  # noqa: E402
from tessaridb.value import Integer, Object  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus import read_corpus  # noqa: E402
from tessaridb import _frames as frames  # noqa: E402
from tessaridb._answer import read_answer  # noqa: E402
from tessaridb.connection import Connection, Progress, _change, _elsewhere, _progress  # noqa: E402
from tessaridb._bytes import ProtocolError  # noqa: E402
from tessaridb._encode import encode  # noqa: E402
from tessaridb.errors import (  # noqa: E402
    Malformed,
    NodeTooOld,
    NotThisProtocol,
    Refused,
    TooLarge,
    Truncated,
    UnknownFrame,
    WrongVersion,
)
from tessaridb.outcome import (  # noqa: E402
    Complete,
    Corrections,
    Done,
    Exact,
    Inexact,
    Keys,
    NotConsulted,
    Records,
    Removed,
    Unknown,
    Unstated,
    ValueOutcome,
)


class Peer:
    """A socket that has already said its piece and then stops.

    The write half is kept open so the client's own greeting has somewhere to go;
    only the read half ends, which is what a peer hanging up looks like.
    """

    def __init__(self, said: bytes) -> None:
        self.ours, self.theirs = socket.socketpair()
        self.theirs.sendall(said)
        self.theirs.shutdown(socket.SHUT_WR)

    def close(self) -> None:
        self.ours.close()
        self.theirs.close()


def u32(n: int) -> bytes:
    return n.to_bytes(4, "big")


def text(s: str) -> bytes:
    raw = s.encode("utf-8")
    return u32(len(raw)) + raw


def answer(*outcomes: bytes) -> bytes:
    return u32(len(outcomes)) + b"".join(u32(len(o)) + o for o in outcomes)


def records(*, rows: bytes = u32(0), tail: bytes = b"") -> bytes:
    """Tag, access path `scan`, an empty names block, then the rows and whatever
    a node of that vintage appended after them."""
    return b"\x01" + b"\x02" + u32(0) + rows + tail


class Greeting(unittest.TestCase):
    def test_a_peer_that_is_not_a_node_is_named_by_its_first_four_bytes(self) -> None:
        # Judged on the magic alone, before the version bytes. A peer that sends
        # three bytes of an HTTP request line and hangs up must not be reported
        # as a truncated stream — that sends whoever reads the error to the
        # network, when the answer is that the address is wrong.
        peer = Peer(b"GET")
        self.addCleanup(peer.close)
        with self.assertRaises(NotThisProtocol):
            frames.greet(peer.ours)

    def test_a_peer_that_says_nothing_is_not_a_node_either(self) -> None:
        peer = Peer(b"")
        self.addCleanup(peer.close)
        with self.assertRaises(NotThisProtocol):
            frames.greet(peer.ours)

    def test_a_greeting_that_starts_correctly_and_stops_is_truncation(self) -> None:
        peer = Peer(b"TES")
        self.addCleanup(peer.close)
        with self.assertRaises(Truncated):
            frames.greet(peer.ours)

    def test_a_major_this_client_does_not_speak_is_refused_with_both_versions(self) -> None:
        peer = Peer(b"TESS\x02\x00")
        self.addCleanup(peer.close)
        with self.assertRaises(WrongVersion) as caught:
            frames.greet(peer.ours)
        self.assertEqual(caught.exception.found, (2, 0))
        self.assertEqual(caught.exception.supported, frames.MAJOR)

    def test_a_differing_minor_is_not_a_refusal(self) -> None:
        # It gates what this client chooses to send, never what it decodes.
        peer = Peer(b"TESS\x01\x63")
        self.addCleanup(peer.close)
        self.assertEqual(frames.greet(peer.ours), (1, 0x63))


class Frames(unittest.TestCase):
    def test_a_declared_length_above_the_ceiling_is_refused(self) -> None:
        # Before anything is allocated. A length from a stranger is not a
        # promise, and allocating on one is the oldest denial of service there is.
        peer = Peer(b"\x02" + u32(frames.CEILING + 1))
        self.addCleanup(peer.close)
        with self.assertRaises(TooLarge) as caught:
            frames.read(peer.ours)
        self.assertEqual(caught.exception.length, frames.CEILING + 1)

    def test_this_client_will_not_send_what_it_would_refuse_to_read(self) -> None:
        peer = Peer(b"")
        self.addCleanup(peer.close)
        with self.assertRaises(TooLarge):
            frames.send(peer.ours, frames.REQUEST, b"\x00" * (frames.CEILING + 1))

    def test_an_unknown_frame_kind_is_an_error_rather_than_a_skip(self) -> None:
        # Tag 7 belongs to the link nodes use among themselves. A client that
        # skipped it would make a version mismatch look like silence.
        peer = Peer(b"\x07" + u32(0))
        self.addCleanup(peer.close)
        with self.assertRaises(UnknownFrame) as caught:
            frames.read(peer.ours)
        self.assertEqual(caught.exception.tag, 7)

    def test_zero_bytes_between_frames_is_a_clean_goodbye(self) -> None:
        peer = Peer(b"")
        self.addCleanup(peer.close)
        self.assertIsNone(frames.read(peer.ours))


class Outcomes(unittest.TestCase):
    def test_an_unknown_tag_is_surfaced_and_the_answer_keeps_reading(self) -> None:
        # The length is what makes it survivable: a newer node may introduce an
        # outcome kind anywhere in an answer, and a client must not stop at it.
        got = read_answer(answer(b"\x00", b"\x63hello", b"\x00"))
        self.assertEqual(got[0], Done())
        self.assertEqual(got[1], Unknown(0x63, b"hello"))
        self.assertEqual(got[2], Done())

    def test_bytes_left_inside_a_recognised_outcome_are_skipped(self) -> None:
        # The opposite of §4.8's rule for a value payload, and deliberately so:
        # it lets a later minor append a field to an outcome kind that exists.
        got = read_answer(answer(b"\x03" + u32(1) + text("ada") + b"future"))
        self.assertEqual(got[0], Keys(("ada",)))

    def test_a_removed_count_is_plain_and_not_the_inverted_i64(self) -> None:
        # The frame layer and the value layer share this module and differ in
        # exactly one place. Read with the inversion, a count of 1 becomes a
        # number near -2**63 and nothing complains.
        got = read_answer(answer(b"\x04" + (1).to_bytes(8, "big")))
        self.assertEqual(got[0], Removed(1))

    def test_a_value_outcome_carries_a_length_before_its_value(self) -> None:
        # §3.5 writes it as "names · `bytes` value", and `bytes` is a u32 length
        # then the bytes. Read raw, the length's first byte is taken for a type
        # tag — 0x00, which is not one. It shipped in a sibling client because a
        # suite that only SELECTs never produces a value outcome at all.
        body = b"\x02" + u32(0) + u32(1) + b"\x01"
        self.assertEqual(read_answer(answer(body))[0], ValueOutcome({}, NONE))

    def test_an_unrecognised_access_path_reads_as_scan(self) -> None:
        # The one path that promises nothing, which is the honest answer for a
        # path this build has no name for.
        got = read_answer(answer(b"\x01" + b"\x63" + u32(0) + u32(0)))
        self.assertEqual(got[0].path, "scan")


class AppendedFields(unittest.TestCase):
    """The three fields at the end of a Records body, and the one that does not
    default."""

    def test_a_body_that_ends_after_the_records_is_a_node_with_nothing_to_say(self) -> None:
        got = read_answer(answer(records()))[0]
        self.assertEqual(got, Records("scan", {}, ()))
        self.assertEqual(got.notes, ())
        self.assertFalse(got.only)

    def test_an_absent_exactness_is_not_exact(self) -> None:
        # This is the field's entire purpose. A node that predates it did not
        # serve exact answers and forget to say so — it made no claim at all.
        self.assertIsInstance(read_answer(answer(records()))[0].exactness, Unstated)

    def test_every_point_a_body_may_end_at_leaves_exactness_unstated(self) -> None:
        # A Records body has four places an older node's write simply stops, and
        # the absent-exactness rule has to hold at each of them. Testing only the
        # first leaves three branches where a client can quietly invent the
        # promise this field exists to avoid — measured: a mutation that reads
        # absent as exact at the third branch passes a suite that tests the first.
        ends = {
            "after the records": records(),
            "after the notes": records(tail=u32(0)),
            "after the only flag": records(tail=u32(0) + b"\x00"),
        }
        for where, body in ends.items():
            with self.subTest(where):
                got = read_answer(answer(body))[0]
                self.assertIsInstance(got.exactness, Unstated)
                self.assertIsInstance(got.suggestion, NotConsulted)

    def test_exactness_zero_and_one_are_the_other_two_states(self) -> None:
        exact = records(tail=u32(0) + b"\x00" + b"\x00" + text(""))
        self.assertEqual(read_answer(answer(exact))[0].exactness, Exact())

        inexact = records(tail=u32(0) + b"\x00" + b"\x01" + text("a ceiling was reached"))
        self.assertEqual(
            read_answer(answer(inexact))[0].exactness, Inexact("a ceiling was reached")
        )

    def test_a_consulted_dictionary_that_found_nothing_is_not_silence(self) -> None:
        # `complete` is a claim about the collection; `not-consulted` is the
        # absence of a claim. Rendering both as "no suggestions" reports a
        # negative the node never checked, on every read of an unindexed field.
        head = u32(0) + b"\x00" + b"\x00" + text("")
        self.assertIsInstance(
            read_answer(answer(records(tail=head + b"\x00")))[0].suggestion, NotConsulted
        )
        self.assertEqual(read_answer(answer(records(tail=head + b"\x01")))[0].suggestion, Complete())

        corrections = head + b"\x02" + u32(1) + text("appl") + text("apple")
        got = read_answer(answer(records(tail=corrections)))[0].suggestion
        self.assertIsInstance(got, Corrections)
        self.assertEqual(got.items[0].instead, "apple")

    def test_a_suggestion_state_this_build_does_not_know_reads_as_silence(self) -> None:
        head = u32(0) + b"\x00" + b"\x00" + text("")
        self.assertIsInstance(
            read_answer(answer(records(tail=head + b"\x63")))[0].suggestion, NotConsulted
        )


class Feeds(unittest.TestCase):
    def test_a_subscription_the_node_refuses_is_a_refusal_not_an_unknown_frame(self) -> None:
        # It arrives on the feed rather than at the Subscribe frame, because the
        # node reads the frame before it can judge it. Found against a running
        # node: subscribing before `USE NAMESPACE` raised `UnknownFrame(3)`.
        said = "no collection `thing` — name a namespace first"
        peer = Peer(b"TESS\x01\x01" + b"\x03" + u32(len(said.encode())) + said.encode())
        self.addCleanup(peer.close)
        conn = Connection(peer.ours, None, None)
        subscription = conn.subscribe(table="thing")
        with self.assertRaises(Refused) as caught:
            next(iter(subscription))
        self.assertEqual(caught.exception.message, said)


class Bodies(unittest.TestCase):
    def test_a_change_is_written_or_removed_and_nothing_else(self) -> None:
        removed = (9).to_bytes(8, "big") + text("thing") + text("1") + b"\x01"
        change = _change(removed)
        self.assertTrue(change.removed)
        self.assertEqual(change.sequence, 9)
        self.assertIsNone(change.value)

        with self.assertRaises(Malformed):
            _change((9).to_bytes(8, "big") + text("thing") + text("1") + b"\x63")

    def test_a_change_from_a_split_table_carries_its_cursor(self) -> None:
        removed = (9).to_bytes(8, "big") + text("thing") + text("1") + b"\x01"
        self.assertIsNone(_change(removed).cursor)
        self.assertEqual(_change(removed + text("0:9,2:3")).cursor, "0:9,2:3")

    def test_a_redirect_is_settled_or_transient_and_zero_is_neither(self) -> None:
        # Zero is deliberately unassigned: it is what a truncated or zeroed
        # buffer holds, and giving it a meaning would let corruption decode as a
        # value.
        body = b"\x11" * 16 + (4).to_bytes(8, "big") + b"\x02" + text("127.0.0.1:9081")
        where = _elsewhere(body)
        self.assertEqual(where.settlement, "transient")
        self.assertEqual(where.endpoint, "127.0.0.1:9081")
        self.assertEqual(where.epoch, 4)

        with self.assertRaises(Malformed):
            _elsewhere(b"\x11" * 16 + (4).to_bytes(8, "big") + b"\x00" + text(""))


class NarrowedFeeds(unittest.TestCase):
    """Protocol 1.4: a feed narrowed by a condition (§3.7) and how far it read (§3.15)."""

    def node(self, minor: int, said: bytes = b"") -> tuple[Connection, socket.socket]:
        ours, theirs = socket.socketpair()
        self.addCleanup(ours.close)
        self.addCleanup(theirs.close)
        theirs.sendall(b"TESS\x01" + bytes([minor]) + said)
        conn = Connection(ours, None, None)
        theirs.recv(6)  # the client's own greeting
        return conn, theirs

    def test_a_condition_follows_an_empty_cursor_with_its_parameters_as_one_object(self) -> None:
        conn, theirs = self.node(4)
        conn.subscribe(7, "orders", condition="total > $least", parameters={"least": Integer(100)})
        want = (
            (7).to_bytes(8, "big")
            + b"\x01"
            + text("orders")
            + text("")
            + text("total > $least")
            + lenbytes(encode(Object({"least": Integer(100)})))
        )
        self.assertEqual(theirs.recv(4096), b"\x04" + u32(len(want)) + want)

    def test_a_resumed_split_feed_keeps_its_cursor_ahead_of_the_condition(self) -> None:
        conn, theirs = self.node(4)
        conn.subscribe(table="orders", cursor="1.1:d=12", condition="open")
        want = (
            (0).to_bytes(8, "big")
            + b"\x01"
            + text("orders")
            + text("1.1:d=12")
            + text("open")
            + lenbytes(encode(Object({})))
        )
        self.assertEqual(theirs.recv(4096), b"\x04" + u32(len(want)) + want)

    def test_a_condition_is_not_sent_to_a_node_before_minor_four(self) -> None:
        conn, theirs = self.node(3)
        with self.assertRaises(NodeTooOld) as caught:
            conn.subscribe(table="orders", condition="open")
        self.assertEqual((caught.exception.found, caught.exception.needed), (3, 4))
        conn.close()
        self.assertEqual(theirs.recv(4096), b"", "nothing reached a node that would misread it")

    def test_progress_is_handed_over_beside_changes_and_moves_the_resume_point(self) -> None:
        progress = (41).to_bytes(8, "big") + text("1.1:d=12")
        change = (42).to_bytes(8, "big") + text("orders") + text("7") + b"\x01"
        said = b"\x25" + u32(len(progress)) + progress + b"\x05" + u32(len(change)) + change
        conn, theirs = self.node(4, said)
        theirs.shutdown(socket.SHUT_WR)
        feed = conn.subscribe(table="orders", condition="open")
        arrived = iter(feed)
        self.assertEqual(next(arrived), Progress(41, "1.1:d=12"))
        self.assertEqual((feed.resume_from, feed.resume_cursor), (42, "1.1:d=12"))
        self.assertEqual(next(arrived).sequence, 42)
        self.assertEqual(feed.resume_from, 43)

    def test_every_progress_vector_decodes_exactly_or_is_refused(self) -> None:
        cases = read_corpus("frames-v1.json")["progress"]
        self.assertGreaterEqual(len(cases), 5, "the corpus holds every case it should")
        for case in cases:
            with self.subTest(case["name"]):
                body = bytes.fromhex(case["body_hex"])
                if "malformed" in case:
                    with self.assertRaises(ProtocolError):
                        _progress(body)
                    continue
                got = _progress(body)
                self.assertEqual(str(got.sequence), case["decoded"]["sequence"])
                self.assertEqual(got.cursor, case["decoded"]["cursor"])


def lenbytes(raw: bytes) -> bytes:
    return u32(len(raw)) + raw


if __name__ == "__main__":
    unittest.main()
