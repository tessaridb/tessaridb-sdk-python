"""The frame layer — §3.1 the greeting, §3.2 the frame, §3.3 the kinds.

Three properties of this layer decide whether a client is safe, and all three are
about refusing rather than about reading.

**The ceiling is checked before anything is allocated.** A declared length is a
number a stranger sent; allocating on one is the oldest denial of service there
is. It binds on the way out as well as in — a client that would refuse to read a
frame that size must not send one either, because a peer that emits what it would
refuse to read is running two protocols.

**An unknown frame kind closes the connection.** It is not skipped. A protocol
that ignores what it does not understand is one where a version mismatch looks
like silence.

**The magic is judged on its own four bytes, before the version bytes are read.**
A peer that is not a node owes nothing: it may send three bytes of an HTTP
request line and hang up. A client that waits for all six first reports that as a
truncated stream, which sends whoever reads the error to the network — when the
answer is that the address is wrong.
"""

from __future__ import annotations

import socket

from .errors import IoError, NotThisProtocol, TooLarge, Truncated, UnknownFrame, WrongVersion

CEILING = 16 * 1024 * 1024
HEADER = 5
MAGIC = b"TESS"
MAJOR = 1
MINOR = 1

REQUEST = 1
ANSWER = 2
REFUSAL = 3
SUBSCRIBE = 4
CHANGE = 5
ELSEWHERE = 13

#: The kinds a node may send us. Checked as a set membership and never as a
#: range: that the client's tags are low and contiguous describes today's
#: arrangement and is not a property to rely on. Tags 6 through 12 belong to the
#: link nodes use among themselves and share this one byte.
FROM_NODE = frozenset({ANSWER, REFUSAL, CHANGE, ELSEWHERE})


def _receive(sock: socket.socket, width: int, what: str) -> bytes:
    """Exactly ``width`` bytes, or an error naming which.

    A short read is not an error — a socket is a stream and delivers what it has.
    Zero bytes is the end of it.
    """
    out = bytearray()
    while len(out) < width:
        try:
            piece = sock.recv(width - len(out))
        except OSError as why:
            raise IoError(f"reading {what}: {why}") from why
        if not piece:
            raise Truncated(f"the stream ended inside {what}: wanted {width} bytes, got {len(out)}")
        out += piece
    return bytes(out)


def greet(sock: socket.socket) -> tuple[int, int]:
    """Exchange greetings and return the peer's ``(major, minor)``.

    Both sides send. The refusals happen here and never mid-conversation: a
    version mismatch discovered later arrives as a decode failure that reads like
    corruption.

    A differing **minor** is not a refusal. The peer's minor is returned so a
    caller can decide not to send what an older peer cannot read; it never gates
    decoding, because decoding is already safe on its own.
    """
    try:
        sock.sendall(MAGIC + bytes([MAJOR, MINOR]))
    except OSError as why:
        raise IoError(f"sending the greeting: {why}") from why

    seen = bytearray()
    while len(seen) < len(MAGIC):
        try:
            piece = sock.recv(len(MAGIC) - len(seen))
        except OSError as why:
            raise IoError(f"reading the greeting: {why}") from why
        if not piece:
            # Judged on what arrived rather than on how much of it there was.
            if not seen:
                raise NotThisProtocol("the peer sent nothing; a node greets on connect")
            raise Truncated(f"the peer began the greeting and stopped after {len(seen)} bytes")
        seen += piece
        if not MAGIC.startswith(bytes(seen)):
            raise NotThisProtocol(f"the peer opened with {bytes(seen)!r}, which is not {MAGIC!r}")

    version = _receive(sock, 2, "the greeting's version")
    major, minor = version[0], version[1]
    if major != MAJOR:
        raise WrongVersion((major, minor), MAJOR)
    return major, minor


def send(sock: socket.socket, kind: int, body: bytes) -> None:
    """One frame: kind, big-endian length, body.

    The ceiling binds here too — see the module docstring.
    """
    if len(body) > CEILING:
        raise TooLarge(len(body), CEILING)
    try:
        sock.sendall(bytes([kind]) + len(body).to_bytes(4, "big") + body)
    except OSError as why:
        raise IoError(f"sending a frame: {why}") from why


def read(sock: socket.socket) -> tuple[int, bytes] | None:
    """One frame, or ``None`` for a clean goodbye.

    Reading zero bytes **between** frames is the peer hanging up politely.
    Reading zero bytes **inside** a header or a body is truncation, and the two
    are different facts.
    """
    try:
        first = sock.recv(1)
    except OSError as why:
        raise IoError(f"reading a frame header: {why}") from why
    if not first:
        return None

    kind = first[0]
    length = int.from_bytes(_receive(sock, 4, "a frame length"), "big")
    if length > CEILING:
        # Before anything is allocated, and the connection is finished either
        # way — a peer sending this is not one to keep reading from.
        raise TooLarge(length, CEILING)
    if kind not in FROM_NODE:
        # Read no further. The caller closes: an unknown kind ends the
        # conversation rather than being stepped over.
        raise UnknownFrame(kind)
    return kind, _receive(sock, length, "a frame body")
