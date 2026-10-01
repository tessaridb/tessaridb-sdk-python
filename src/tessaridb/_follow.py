"""Following a redirect (§3.12) to the node that should answer.

- **At most three hops.** A fourth redirect is a loop, or a cluster moving
  faster than a request can follow it; going on would not tell them apart.
- **Epochs never go backwards.** A redirect dated by an older leadership than one
  already followed was decided before it, and points at the past.
- **The node there is the node named.** ``session::context()`` on arrival says
  which node took the connection; a different one is not sent the request.
- **The tenancy goes with the request.** The new connection is a new session and
  has selected nothing, so the namespace and database selected here are selected
  there first — each checked as a plain name and never quoted into a script.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Mapping

from .errors import Malformed, NotFollowable, RedirectLoop, StaleRedirect, WrongNode
from .outcome import ValueOutcome
from .value import Object, Text, Uuid, Value

if TYPE_CHECKING:
    from .connection import Connection, Elsewhere, Reply

MOST_HOPS = 3
CONTEXT = "RETURN session::context();"
_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def followed(
    conn: Connection, script: str, parameters: Mapping[str, Value], first: Elsewhere
) -> Reply:
    """Send ``script`` where ``first`` says, and on, until something answers."""
    from .connection import connect

    selecting = _selection(conn)
    redirect, floor, hops = first, 0, 0
    while True:
        if hops >= MOST_HOPS:
            raise RedirectLoop(hops)
        if redirect.epoch < floor:
            raise StaleRedirect(redirect.epoch, floor)
        floor = redirect.epoch
        there = connect(redirect.endpoint, conn._user, conn._password)
        try:
            if _context(there).get("node") != Uuid(redirect.node):
                raise WrongNode(redirect.node)
            if selecting:
                _answered(there, selecting)
            hops += 1
            reply = there._ask(script, parameters)
        except BaseException:
            there.close()
            raise
        if reply.redirect is None:
            if redirect.settlement == "settled":
                conn._move_to(there)
            else:
                there.close()
            return reply
        there.close()
        redirect = reply.redirect


def _context(conn: Connection) -> dict[str, Value]:
    """What ``session::context()`` answers on ``conn``, without following."""
    reply = _answered(conn, CONTEXT)
    answered = reply.outcomes[-1] if reply.outcomes else None
    if not isinstance(answered, ValueOutcome) or not isinstance(answered.value, Object):
        raise Malformed("session::context() answers one object")
    return answered.value.fields


def _answered(conn: Connection, script: str) -> Reply:
    reply = conn._ask(script, {})
    if reply.redirect is not None:
        raise Malformed("a node redirected a statement that runs anywhere")
    return reply


def _selection(conn: Connection) -> str:
    """The ``USE`` that selects ``conn``'s tenancy again, or empty."""
    fields = _context(conn)
    script = ""
    for word, key in (("NAMESPACE", "namespace"), ("DATABASE", "database")):
        name = fields.get(key)
        if not isinstance(name, Text):
            continue
        if not _PLAIN.fullmatch(name.value):
            raise NotFollowable(name.value)
        script += f"USE {word} {name.value}; "
    return script
