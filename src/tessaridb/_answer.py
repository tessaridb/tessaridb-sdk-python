"""The Answer body — §3.5, with the names block of §3.9.

Every outcome carries its own length, and that length does three jobs that are
easy to mistake for one.

It is a **skip**: a tag this build does not recognise is yielded as ``Unknown``
and its remaining bytes are stepped over, so a newer node may introduce an
outcome kind anywhere in an answer without breaking an older client. It is a
**bound**: a recognised tag whose body claims more than its length allows is
malformed, and treating the length as advisory turns one corrupt outcome into a
mis-parse of every outcome after it. And it is a **resume point**: bytes left
over inside an outcome after this build has read everything it knows are skipped
rather than treated as an error, which is what lets a later minor append a field
to an outcome kind that already exists.

That last rule is the opposite of §4.8's rule for a value payload, and
deliberately so: a value has no length in front of it to resume from.

Three fields in a Records outcome are absent on an older node, and only two of
them default. Read the notes on ``Exactness`` in ``outcome`` before changing any of it.
"""

from __future__ import annotations

from ._bytes import Reader
from ._decode import decode
from .errors import Malformed
from .outcome import (
    Complete,
    Correction,
    Corrections,
    Done,
    Exact,
    Exactness,
    Inexact,
    Keys,
    NotConsulted,
    Note,
    Outcome,
    Records,
    Removed,
    Row,
    Suggestion,
    Unknown,
    ValueOutcome,
)

__all__ = ["read_answer"]

#: §3.5. An unrecognised byte reads as ``scan`` — the honest answer for a path
#: this build has no name for, because it is the one path that promises nothing.
ACCESS_PATHS = (
    "record",
    "index",
    "scan",
    "ordered",
    "approximate",
    "graph",
    "join",
    "materialised",
    "span",
)



def read_answer(body: bytes) -> tuple[Outcome, ...]:
    r = Reader(body)
    count = r.u32("the outcome count")
    out = []
    for _ in range(count):
        length = r.u32("an outcome length")
        if length < 1:
            raise Malformed("an outcome carries at least its tag")
        out.append(_outcome(r.fixed(length, "an outcome body")))
    return tuple(out)


def _outcome(raw: bytes) -> Outcome:
    """The reader is bounded by the outcome's own length, so nothing here can
    read into the next outcome even if a body lies about its shape."""
    r = Reader(raw)
    tag = r.u8("an outcome tag")
    if tag == 0:
        return Done()
    if tag == 1:
        return _records(r)
    if tag == 2:
        names = _names(r)
        # §3.5 writes this outcome as "names · `bytes` value", and `bytes` at the
        # frame layer is a u32 length and then the bytes — not a bare value.
        return ValueOutcome(names, decode(r.lenbytes("a value outcome's value")))
    if tag == 3:
        count = r.u32("a key count")
        return Keys(tuple(r.text("a key") for _ in range(count)))
    if tag == 4:
        return Removed(r.u64("a removed count"))
    # Tag 255 is this client's own report of an unrecognised tag and is never
    # sent by a node; every other unrecognised tag lands here too. The bytes are
    # kept so a caller can say what it could not read.
    return Unknown(tag, raw[1:])


def _records(r: Reader) -> Records:
    byte = r.u8("an access path")
    path = ACCESS_PATHS[byte] if byte < len(ACCESS_PATHS) else "scan"
    names = _names(r)

    count = r.u32("a record count")
    rows = []
    for _ in range(count):
        # The identity is read into a name FIRST, deliberately — see _names.
        identity = r.text("a record identity")
        rows.append(Row(identity, decode(r.lenbytes("a record value"))))

    # From here every field may simply be absent: a node built before it existed
    # ends the body. That is a node with nothing to say, not a truncation.
    if r.exhausted:
        return Records(path, names, tuple(rows))
    notes = []
    for _ in range(r.u32("a note count")):
        kind = r.text("a note kind")
        notes.append(Note(kind, r.text("a note message")))

    if r.exhausted:
        return Records(path, names, tuple(rows), tuple(notes))
    only = r.u8("the only flag") != 0

    if r.exhausted:
        return Records(path, names, tuple(rows), tuple(notes), only)
    exact_byte = r.u8("the exactness")
    reason = r.text("the exactness reason")
    exactness: Exactness = Exact() if exact_byte == 0 else Inexact(reason)

    if r.exhausted:
        return Records(path, names, tuple(rows), tuple(notes), only, exactness)
    return Records(path, names, tuple(rows), tuple(notes), only, exactness, _suggestion(r))


def _suggestion(r: Reader) -> Suggestion:
    state = r.u8("a suggestion state")
    if state == 1:
        return Complete()
    if state == 2:
        count = r.u32("a suggestion count")
        items = []
        for _ in range(count):
            typed = r.text("a suggested term")
            items.append(Correction(typed, r.text("a suggested replacement")))
        return Corrections(tuple(items))
    # State 0, and any state this build does not know, read as silence: a newer
    # node speaking a vocabulary this client lacks is not a malformed answer.
    return NotConsulted()


def _names(r: Reader) -> dict[int, str]:
    """§3.9. A table reference carries an id and the name lives in the catalog on
    the server; without this block a client can only render an opaque reference,
    and the point of the protocol is that a client decides nothing."""
    count = r.u32("a name count")
    names: dict[int, str] = {}
    for _ in range(count):
        # The id is read into a name FIRST, deliberately. A dict comprehension
        # would read it inline, and which of a key and a value is evaluated first
        # is a property of the language version rather than of this protocol —
        # it changed in 3.8. A stream decoder must not depend on that.
        table = r.u32("a table id")
        names[table] = r.text("a table name")
    return names
