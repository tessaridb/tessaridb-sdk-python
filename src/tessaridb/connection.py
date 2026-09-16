"""The wire connection — §3.4 requests, §3.6 refusals, §3.7 subscriptions,
§3.10 session semantics, §3.12 redirects.

**A connection holds one session.** ``USE NAMESPACE prod;`` is still in force in
the next statement on that connection; two connections are two sessions and share
nothing but the store. That is what a connection means, and it is why this class
exists at all rather than a ``send(script)`` function.

**Subscribing consumes the connection.** After a Subscribe frame the socket
delivers changes and no longer answers statements. A client that wants both opens
two connections, and a client API that hides this is promising a multiplexing the
protocol does not perform — so this one refuses instead.

**A parameter travels in the value codec, never as text.** This is the reason the
wire protocol exists. A value the server has to *parse* is a value that can be
parsed as something else, and binding after parsing exists precisely to make that
impossible. A client that formats parameters into the script destroys the
property invisibly, because the resulting script still looks correct.

**There is no TLS on this protocol.** Credentials travel as given. Run this on a
protected network or behind something that terminates TLS. It is a property of
the protocol rather than an omission here, and it is said out loud rather than
left to be discovered.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Iterator, Mapping

from . import _frames as frames
from ._answer import read_answer
from ._bytes import Reader, Writer
from ._decode import decode
from ._encode import encode
from .errors import IoError, Malformed, Refused, TessariError, UnknownFrame
from .outcome import Outcome
from .value import Value

__all__ = ["Connection", "connect", "Reply", "Elsewhere", "Change", "Subscription"]


@dataclass(frozen=True)
class Elsewhere:
    """§3.12. A redirect, which is **not** a failure.

    It is an instruction. A client that handles failures correctly — logs them,
    retries a bounded number of times, gives up — handles an instruction encoded
    as one incorrectly, every time, by construction. So it arrives here rather
    than through the error path, and a caller that has no routing behaviour
    reports it and stops rather than silently returning an empty answer.

    ``node`` makes the redirect checkable: an address alone cannot be, because a
    client that dialled it and met a different node would have no way to notice.
    ``epoch`` dates it, so a client following a redirect written under an older
    leadership can tell a loop from progress. ``settled`` may be remembered and
    used to update a routing map; ``transient`` **must not be** — it answers this
    request and nothing after it.
    """

    node: bytes
    epoch: int
    settlement: str
    endpoint: str


@dataclass(frozen=True)
class Reply:
    """Either the outcomes or a redirect, never both."""

    outcomes: tuple[Outcome, ...] = ()
    redirect: Elsewhere | None = None


@dataclass(frozen=True)
class Change:
    """§3.8. The ``sequence`` is shared by every change of one commit, which is
    what lets a subscriber apply them as the unit they were written as.

    The table is **named, not identified**: an id is meaningless outside the
    process that minted it, and the catalog is on the node.
    """

    sequence: int
    table: str
    identity: str
    removed: bool
    value: Value | None


def connect(address: str, user: str | None = None, password: str | None = None) -> Connection:
    """Dial ``host:port`` — a bare address, with no URL scheme.

    Credentials are optional because a store with no users declared is **open**
    and runs anything, which is what keeps an empty one usable. A closed store's
    refusal comes from the session, not from a second rule in this client.
    """
    host, _, port = address.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"an address is host:port, got {address!r}")
    try:
        sock = socket.create_connection((host, int(port)))
    except OSError as why:
        raise IoError(f"connecting to {address}: {why}") from why
    return Connection(sock, user, password)


class Connection:
    """One session. Build it with :func:`connect`."""

    def __init__(self, sock: socket.socket, user: str | None, password: str | None) -> None:
        self._sock = sock
        self._user = user
        self._password = password
        self._owed = user is not None
        self._subscribed = False
        try:
            _, self.minor = frames.greet(sock)
        except TessariError:
            self.close()
            raise

    def execute(self, script: str, parameters: Mapping[str, Value] | None = None) -> Reply:
        """Run a script. Raises :class:`~tessaridb.errors.Refused` when the store
        says no — in its own words, carried through verbatim.

        A refusal does not close the connection: a client that mistyped a
        statement has not stopped being a client.
        """
        if self._subscribed:
            raise TessariError("this connection is a subscription and no longer answers statements")
        frames.send(self._sock, frames.REQUEST, self._request(script, parameters or {}))
        return self._reply()

    def subscribe(self, from_: int = 0, table: str | None = None) -> Subscription:
        """Consume this connection and deliver changes from ``from_`` **inclusive**.

        ``0`` means everything the log still holds. The ``+1`` arithmetic that
        makes a resume correct is owned by :class:`Subscription` rather than
        documented and left to the caller — resuming at a position already
        handled delivers it twice and resuming past one reports being caught up,
        and both are silent.
        """
        if self._subscribed:
            raise TessariError("this connection is already a subscription")
        w = Writer()
        w.u64(from_)
        w.u8(0 if table is None else 1)
        if table is not None:
            w.text(table)
        frames.send(self._sock, frames.SUBSCRIBE, w.bytes())
        self._subscribed = True
        return Subscription(self, from_)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, script: str, parameters: Mapping[str, Value]) -> bytes:
        w = Writer()
        w.text(script)
        # Spent on the first request and not again. The store verifies a password
        # with Argon2id at the OWASP floor, so presenting one per statement pays
        # that cost per statement — and §3.10 says the session is the connection,
        # which is exactly what makes once enough.
        if self._owed and self._user is not None:
            w.u8(1)
            w.text(self._user)
            w.text(self._password or "")
            self._owed = False
        else:
            w.u8(0)
        w.u32(len(parameters))
        for name in parameters:
            w.text(name)
            w.lenbytes(encode(parameters[name]))
        return w.bytes()

    def _reply(self) -> Reply:
        kind, body = self._read()
        if kind == frames.ANSWER:
            return Reply(outcomes=read_answer(body))
        if kind == frames.REFUSAL:
            # §3.6: the body is the store's own message, whole, with no length
            # prefix in front of it.
            raise Refused(body.decode("utf-8", "replace"))
        if kind == frames.ELSEWHERE:
            return Reply(redirect=_elsewhere(body))
        # A Change on a connection that has not subscribed is an unknown frame
        # (§3.3), and an unknown frame closes the connection.
        self.close()
        raise UnknownFrame(kind)

    def _read(self) -> tuple[int, bytes]:
        try:
            frame = frames.read(self._sock)
        except TessariError:
            self.close()
            raise
        if frame is None:
            self.close()
            raise IoError("the node hung up before answering")
        return frame


class Subscription:
    """Changes, in order, until the connection ends.

    The node drops a subscriber that stops reading after **30 seconds** — its
    socket fills, the node's write blocks, and rather than hold a thread
    indefinitely the node ends the connection. Nothing is lost: the log is the
    buffer. So the iterator simply finishes, and the reconnect path is to open a
    new connection and subscribe again from :attr:`resume_from`.
    """

    def __init__(self, conn: Connection, from_: int) -> None:
        self._conn = conn
        #: The position to resume from: the last sequence handled, plus one.
        self.resume_from = from_

    def __iter__(self) -> Iterator[Change]:
        while True:
            try:
                frame = frames.read(self._conn._sock)
            except TessariError:
                self._conn.close()
                raise
            if frame is None:
                self._conn.close()
                return
            kind, body = frame
            if kind != frames.CHANGE:
                self._conn.close()
                raise UnknownFrame(kind)
            change = _change(body)
            self.resume_from = change.sequence + 1
            yield change

    def close(self) -> None:
        self._conn.close()


def _change(body: bytes) -> Change:
    r = Reader(body)
    sequence = r.u64("a change sequence")
    table = r.text("a change's table")
    identity = r.text("a change's identity")
    fate = r.u8("what became of a record")
    if fate == 0:
        return Change(sequence, table, identity, False, decode(r.lenbytes("a change's value")))
    if fate == 1:
        return Change(sequence, table, identity, True, None)
    raise Malformed(f"a change is written (0) or removed (1), not {fate}")


def _elsewhere(body: bytes) -> Elsewhere:
    r = Reader(body)
    node = r.fixed(16, "a redirect's node")
    epoch = r.u64("a redirect's epoch")
    byte = r.u8("a redirect's settlement")
    if byte == 1:
        settlement = "settled"
    elif byte == 2:
        settlement = "transient"
    else:
        # Zero is deliberately unassigned, because zero is what a truncated or
        # zeroed buffer holds and giving it a meaning would let corruption decode
        # as a value.
        raise Malformed(f"a redirect is settled (1) or transient (2), not {byte}")
    return Elsewhere(node, epoch, settlement, r.text("a redirect's endpoint"))
