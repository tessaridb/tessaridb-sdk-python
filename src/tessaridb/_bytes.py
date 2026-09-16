"""The value-layer primitives of §2.2, and the range checks Python needs.

The i64 inversion is the single most important detail in the protocol. It is NOT
two's-complement big-endian: ``Integer(1)`` encodes as ``80 00 00 00 00 00 00
01``. A client that writes plain big-endian gets every integer, duration,
datetime and integer record id wrong — and round-trips perfectly against itself,
which is why the corpus exists and why a suite written alongside this codec
cannot take its place.

The inversion is shared with an order-preserving key encoder, where a set sign
bit would sort negatives above positives. The value payload does not need that
ordering but uses the same writer, so the bytes carry it. Reproduce the bytes,
not the rationale.

**Every fixed-width write checks its range first.** In a language whose integers
are arbitrary precision this is the whole extra job: ``int.to_bytes`` raises on
overflow, but a value that has already been narrowed elsewhere, or one built from
arithmetic that was never bounded, reaches here looking ordinary. The check is at
the write because that is the last place the value is still whole.
"""

from __future__ import annotations

import struct

from .errors import TessariError

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
INT128_MIN = -(2**127)
INT128_MAX = 2**127 - 1
UINT32_MAX = 2**32 - 1
UINT64_MAX = 2**64 - 1
NANOS_PER_SECOND = 1_000_000_000


class ProtocolError(TessariError):
    """The bytes did not say what this client can read, or a value cannot be written.

    This is §3.11's ``Encoding`` class: report it, do not retry.
    """


def _check(value: int, low: int, high: int, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProtocolError(f"{what} is an integer, got {type(value).__name__}")
    if value < low or value > high:
        raise ProtocolError(f"{what} does not fit: {value}")
    return value


class Writer:
    __slots__ = ("_out",)

    def __init__(self) -> None:
        self._out = bytearray()

    def bytes(self) -> bytes:
        return bytes(self._out)

    def u8(self, value: int) -> None:
        self._out.append(_check(value, 0, 255, "a u8"))

    def u32(self, value: int) -> None:
        self._out += _check(value, 0, UINT32_MAX, "a u32").to_bytes(4, "big")

    def u64(self, value: int) -> None:
        """§2.1, the FRAME layer: plain big-endian, and never inverted.

        It sits beside ``i64`` deliberately. The two layers share this module and
        differ in exactly one place, and conflating them does not fail to parse —
        it returns wrong values.
        """
        self._out += _check(value, 0, UINT64_MAX, "a u64").to_bytes(8, "big")

    def i64(self, value: int) -> None:
        """§2.2, the VALUE layer: two's-complement big-endian, then the first
        byte XORed with 0x80."""
        raw = bytearray(_check(value, INT64_MIN, INT64_MAX, "an i64").to_bytes(8, "big", signed=True))
        raw[0] ^= 0x80
        self._out += raw

    def i128(self, value: int) -> None:
        """Plain, and NOT inverted — the decimal mantissa is the asymmetry."""
        self._out += _check(value, INT128_MIN, INT128_MAX, "an i128").to_bytes(16, "big", signed=True)

    def nanos(self, value: int) -> None:
        """Outside the sub-second range is an error, not a wrap."""
        self._out += _check(value, 0, NANOS_PER_SECOND - 1, "a nanosecond count").to_bytes(4, "big")

    def double(self, value: float) -> None:
        """The IEEE-754 bits, plain big-endian. Never formatted through text: a
        coordinate that goes out through a decimal string and comes back has
        changed, and the change survives every round trip this client can make on
        its own.
        """
        self._out += struct.pack(">d", value)

    def fixed(self, raw: bytes, width: int, what: str) -> None:
        if len(raw) != width:
            raise ProtocolError(f"{what} is {width} bytes, got {len(raw)}")
        self._out += raw

    def lenbytes(self, raw: bytes) -> None:
        self.u32(len(raw))
        self._out += raw

    def text(self, value: str) -> None:
        self.lenbytes(value.encode("utf-8"))

    def varbytes(self, raw: bytes) -> None:
        """0x00 becomes 0x00 0xFF; then the terminator 0x00 0x01.

        The escape is byte-local, which is what makes the encoding of a prefix a
        byte prefix of the encoding of the whole.
        """
        for byte in raw:
            self._out.append(byte)
            if byte == 0x00:
                self._out.append(0xFF)
        self._out += b"\x00\x01"


class Reader:
    __slots__ = ("_raw", "_at")

    def __init__(self, raw: bytes) -> None:
        self._raw = raw
        self._at = 0

    @property
    def remaining(self) -> int:
        return len(self._raw) - self._at

    @property
    def exhausted(self) -> bool:
        return self._at >= len(self._raw)

    def _take(self, width: int, what: str) -> bytes:
        if self.remaining < width:
            raise ProtocolError(f"{what} wanted {width} bytes, {self.remaining} left")
        out = self._raw[self._at : self._at + width]
        self._at += width
        return out

    def u8(self, what: str) -> int:
        return self._take(1, what)[0]

    def u32(self, what: str) -> int:
        return int.from_bytes(self._take(4, what), "big")

    def u64(self, what: str) -> int:
        """§2.1, plain — see ``Writer.u64``."""
        return int.from_bytes(self._take(8, what), "big")

    def i64(self, what: str) -> int:
        raw = bytearray(self._take(8, what))
        raw[0] ^= 0x80
        return int.from_bytes(raw, "big", signed=True)

    def i128(self, what: str) -> int:
        return int.from_bytes(self._take(16, what), "big", signed=True)

    def nanos(self, what: str) -> int:
        value = self.u32(what)
        if value >= NANOS_PER_SECOND:
            raise ProtocolError(f"{what} is outside the sub-second range: {value}")
        return value

    def double(self, what: str) -> float:
        return struct.unpack(">d", self._take(8, what))[0]

    def fixed(self, width: int, what: str) -> bytes:
        return self._take(width, what)

    def lenbytes(self, what: str) -> bytes:
        return self._take(self.u32(f"{what} length"), what)

    def text(self, what: str) -> str:
        raw = self.lenbytes(what)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as why:
            # Fatal rather than replaced: a replacement character is a wrong
            # answer that looks like a right one, and it would be stored.
            raise ProtocolError(f"{what} is not UTF-8: {why}") from why

    def varbytes(self, what: str) -> bytes:
        out = bytearray()
        while True:
            if self.exhausted:
                raise ProtocolError(f"{what} ended without its terminator")
            byte = self.u8(what)
            if byte != 0x00:
                out.append(byte)
                continue
            marker = self.u8(what)
            if marker == 0x01:
                return bytes(out)
            if marker == 0xFF:
                out.append(0x00)
                continue
            raise ProtocolError(f"{what} carries an invalid escape 0x00 {marker:#04x}")
