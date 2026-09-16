"""The ten error classes of §3.11, kept apart.

A client that collapses them into one transport error has thrown away what the
caller needs to act on. Two of them are worth naming here rather than leaving to
the table below.

``NoWritablePeer`` is the one most likely to be flattened and the one that must
not be: its remedy is a statement nobody ran, so reporting it as a connection
failure sends the operator to the network, where there is nothing to find. The
specification requires a client to keep it distinguishable and does not say how
it arrives on the wire — it has no frame kind of its own and a Refusal carries no
structure to hold a class in. Until that is answered this client defines the
class and surfaces such a node's answer as a plain ``Refused``, rather than
string-matching a message the specification carries verbatim precisely so that
nobody parses it.

``Refused`` is not a transport failure. The store said no, in its own words, and
the connection is still good — a client that mistyped a statement has not stopped
being a client.
"""

from __future__ import annotations

__all__ = [
    "TessariError",
    "IoError",
    "NotThisProtocol",
    "WrongVersion",
    "UnknownFrame",
    "TooLarge",
    "Truncated",
    "Malformed",
    "NoWritablePeer",
    "Refused",
]


class TessariError(Exception):
    """Every error this client raises. Catch it to catch the client; catch one of
    the classes below to act on what actually happened."""


class IoError(TessariError):
    """The socket failed. Retry the transport."""


class NotThisProtocol(TessariError):
    """The peer did not greet with ``TESS``. The address is wrong."""


class WrongVersion(TessariError):
    """A node of a major version this client does not speak. Upgrade one side."""

    def __init__(self, found: tuple[int, int], supported: int) -> None:
        super().__init__(
            f"the node speaks major {found[0]} (minor {found[1]}); this client speaks major {supported}"
        )
        self.found = found
        self.supported = supported


class UnknownFrame(TessariError):
    """A frame kind this build lacks. The connection closes — it is not skipped,
    because a protocol that ignores what it does not understand is one where a
    version mismatch looks like silence."""

    def __init__(self, tag: int) -> None:
        super().__init__(f"frame kind {tag} is not one this client knows; the connection is closed")
        self.tag = tag


class TooLarge(TessariError):
    """A declared length above the 16 MiB ceiling, refused before anything was
    allocated. A length from a stranger is not a promise."""

    def __init__(self, length: int, ceiling: int) -> None:
        super().__init__(f"a frame declared {length} bytes, above the {ceiling}-byte ceiling")
        self.length = length
        self.ceiling = ceiling


class Truncated(TessariError):
    """The stream ended mid-frame. Retry the transport.

    Reading zero bytes *between* frames is a clean goodbye and is not this.
    """


class Malformed(TessariError):
    """A body is not the shape its header claims. Report it; do not retry."""


class NoWritablePeer(TessariError):
    """This node takes no writes and knows of no peer that may.

    The remedy is ``DEFINE REPLICA … ROLES writable`` — not a network problem.
    """


class Refused(TessariError):
    """The store said no, in its own words, carried through verbatim.

    The session already writes messages that name the place in the script, and a
    client rewording them becomes a second author for one error.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
