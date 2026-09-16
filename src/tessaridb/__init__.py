"""A client for TessariDB, written from the protocol specification and nothing else.

It links nothing from the database's own repository, and nothing from the clients
written for other languages: four clients that agree because one was transcribed
from another agree about a mistake as readily as about the protocol.
"""

from ._bytes import ProtocolError
from ._decode import decode
from ._encode import encode
from .connection import Change, Connection, Elsewhere, Reply, Subscription, connect
from .errors import (
    IoError,
    Malformed,
    NoWritablePeer,
    NotThisProtocol,
    Refused,
    TessariError,
    TooLarge,
    Truncated,
    UnknownFrame,
    WrongVersion,
)
from .query import BuilderError, Filter, Operator, Rendered, compare
from .statement import Create, Delete, Select, Update, create, delete, select, update
from .outcome import *  # noqa: F401,F403 — the outcome model is the public surface
from .outcome import __all__ as _outcome_names
from .value import *  # noqa: F401,F403 — the value model is the public surface
from .value import __all__ as _value_names

__all__ = [
    "encode",
    "decode",
    "connect",
    "Connection",
    "Subscription",
    "Reply",
    "Elsewhere",
    "Change",
    "ProtocolError",
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
    "BuilderError",
    "Operator",
    "Filter",
    "compare",
    "Rendered",
    "select",
    "create",
    "update",
    "delete",
    "Select",
    "Create",
    "Update",
    "Delete",
    *_outcome_names,
    *_value_names,
]
