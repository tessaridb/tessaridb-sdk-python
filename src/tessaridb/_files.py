"""The `/files` routes' paths and listing shape — §5.1.

Two rules here are the difference between a request that works and one the server
never sees. The `{ns}`, `{db}` and `{bucket}` segments are **names**, checked
before they are interpolated; the `{path…}` is a **value**, percent-encoded,
because a file may be named anything and a slash inside it is part of the name
rather than a directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from .errors import Malformed

__all__ = ["FileEntry", "file_path", "bucket_path", "read_entry"]


@dataclass(frozen=True)
class FileEntry:
    """``path`` is always there; ``size`` and ``updated`` appear only where the
    store recorded them, and never as ``null`` — absence here says the store
    never recorded it rather than that it recorded nothing."""

    path: str
    size: int | None = None
    updated: str | None = None


def file_path(namespace: str, database: str, bucket: str, path: str) -> str:
    # The server percent-decodes the path, so a client MUST encode it. An
    # unencoded space makes the request LINE unparseable rather than merely
    # wrong, and an unencoded `%` asks the server to decode an escape the
    # caller never wrote. Python's `quote` leaves exactly the unreserved set
    # `A-Za-z0-9-._~` alone, and `/` is left as itself because a slash in a
    # file name reaches the server as a slash.
    return f"{bucket_path(namespace, database, bucket)}/{quote(path, safe='/')}"

def bucket_path(namespace: str, database: str, bucket: str) -> str:
    # A trailing slash names a FILE, not a bucket, so it is normalised away
    # here: "list the bucket" and "read the file named /" must not be one
    # keystroke apart.
    for segment in (namespace, database, bucket):
        if not segment.replace("_", "").isalnum() or not segment.isascii():
            raise ValueError(f"{segment!r} is not a name — these segments are [A-Za-z0-9_]+")
    return f"/files/{namespace}/{database}/{bucket}"


def read_entry(node: dict) -> FileEntry:
    """`path` is always there; `size` and `updated` only where the store recorded
    them, and never as ``null``.

    The retired shape is refused rather than read. Earlier builds answered a
    whole §5.6 records outcome wrapped in ``files``, publishing the query plan
    that produced the listing and a storage-level chunk count; §5.1 says those
    are gone and that a client which learned them from a live node was reading
    internals. Such an element still carries a ``path`` — the access path, the
    word ``scan`` — so a reader that takes ``path`` on trust returns a file
    called ``scan`` and reports success. This one says what it met instead.
    """
    if "kind" in node and "records" in node:
        raise Malformed(
            "this node answered the retired wrapped listing (a records outcome inside `files`) "
            "rather than the shape §5.1 specifies — see Q-PY-007"
        )
    if not isinstance(node.get("path"), str):
        raise Malformed(f"a listing element carries a `path` string, got {node!r}")
    return FileEntry(node["path"], node.get("size"), node.get("updated"))


