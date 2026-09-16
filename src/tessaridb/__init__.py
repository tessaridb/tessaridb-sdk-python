"""A client for TessariDB, written from the protocol specification and nothing else.

It links nothing from the database's own repository, and nothing from the clients
written for other languages: four clients that agree because one was transcribed
from another agree about a mistake as readily as about the protocol.
"""

from ._bytes import ProtocolError
from ._decode import decode
from ._encode import encode
from .value import *  # noqa: F401,F403 — the value model is the public surface
from .value import __all__ as _value_names

__all__ = ["encode", "decode", "ProtocolError", *_value_names]
