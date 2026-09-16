"""The HTTP surface — §5. Objects, files, backup, health, and the session token.

Everything the wire protocol does not serve is here, and it is a different client
because it is a different surface rather than an alternative to the first one.

**The password is spent once.** A node verifies Basic with Argon2id at the OWASP
floor — deliberately expensive — and HTTP has no connection to hang a session on,
so that cost is paid on *every* request carrying one. This client opens a session
on its first authenticated call and presents the token after. A client that
skipped this would be correct, would pass every test, and would be slower than
the protocol intends by more than an order of magnitude, which is the kind of
mistake that never shows up as a failure.

**A token ends four ways and all four answer `401`**, so a client cannot tell
them apart and does not need to: discard it, sign in again, retry the request
once. Two neighbouring conditions are not that. An **open store has no session to
open** and answers `401` to ``POST /session`` saying so — carry on without a
token, because on an open store there is nothing to prove. And a node holding
``MAX_SESSION_TOKENS`` answers **`503`**, which is retriable, unlike every `401`
above.

**There is no TLS here either.** The token is a bearer credential in the literal
sense and travels in the clear exactly as the password did.
"""

from __future__ import annotations

import base64
import http.client
import json
import threading
from dataclasses import dataclass

from ._files import FileEntry, bucket_path, file_path, read_entry
from .errors import Malformed, TessariError
from .health import Health, read_health
from .script import Reading, ScriptOutcome, read_results

__all__ = ["HTTPClient", "HTTPError", "FileEntry"]


class HTTPError(TessariError):
    """A refusal from this surface.

    The sentence is meant for a person and is **not** a stable identifier: it
    frequently embeds the caller's own input, so a client branches on the status
    code, which §5.2 enumerates, and never on the text.

    ``401`` and ``403`` are different and are kept apart: ``401`` means sign in,
    ``403`` means the grants do not cover this and signing in again will never
    help.
    """

    def __init__(self, status: int, message: str, location: str = "") -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message
        #: Set on a `307`, which is an instruction rather than a failure — the
        #: HTTP form of the wire's Elsewhere frame.
        self.location = location


@dataclass(frozen=True)
class _Answer:
    status: int
    body: bytes
    location: str = ""


class HTTPClient:
    """Dial ``host:port`` — a bare address, with no URL scheme."""

    def __init__(self, address: str, user: str | None = None, password: str | None = None) -> None:
        self.address = address
        self._user = user
        self._password = password
        self._lock = threading.Lock()
        self._token: str | None = None
        self._sessionless = False

    # ---- the routes ----

    def script(self, source: str, reading: Reading | None = None) -> tuple[ScriptOutcome, ...]:
        """Run a script. **No parameters, and that is deliberate.**

        A parameter on this route is a JSON string carrying *TessariQL source*,
        not a value: ``{"x":"3"}`` is the number 3 and ``{"x":"hello"}`` is a
        `400`. Passing a caller's string through would be a type-confusion hazard
        that no test written against it would show, so this client does not build
        the bridge — a statement with a value in it goes over the wire, where a
        parameter is an encoded value and none of this arises.
        """
        answer = self._send("POST", "/script", source.encode("utf-8"), "text/plain")
        return read_results(_json(answer.body), reading or Reading())

    def put(self, namespace: str, database: str, bucket: str, path: str, content: bytes) -> None:
        """``PUT`` only. ``POST`` is a synonym the node offers and a second verb
        for one action widens the surface for nothing."""
        self._send("PUT", file_path(namespace, database, bucket, path), content, "application/octet-stream")

    def get(self, namespace: str, database: str, bucket: str, path: str) -> bytes | None:
        """``None`` when the file is not there, and zero bytes when it is there
        and empty. Those are different facts and the server draws the line."""
        answer = self._send("GET", file_path(namespace, database, bucket, path), allow=(404,))
        return None if answer.status == 404 else answer.body

    def delete(self, namespace: str, database: str, bucket: str, path: str) -> None:
        """Idempotent: `204` whether or not the file was there, and the server
        reports no difference — so a client that claimed to know which had
        happened would be inventing it."""
        self._send("DELETE", file_path(namespace, database, bucket, path))

    def listing(self, namespace: str, database: str, bucket: str) -> tuple[FileEntry, ...] | None:
        """``None`` when the name is not a bucket. An empty tuple means the bucket
        is there and holds nothing — never read one for the other.

        A name declared as something else and a name nothing declared are both
        `404`, separated by a sentence this client surfaces and never parses:
        which of the two a given route returns is explicitly not specified.
        """
        answer = self._send("GET", bucket_path(namespace, database, bucket), allow=(404,))
        if answer.status == 404:
            return None
        return tuple(read_entry(f) for f in _json(answer.body).get("files", []))

    def backup(self, since: int | None = None) -> bytes:
        """The whole log in one response — there is no resumption and no range
        support, so a client's memory ceiling for this route is the log's size.

        On a store with no ``DEFINE USER`` this is unauthenticated and returns
        everything. That is the open-store rule at its loudest, not a defect.
        """
        where = "/backup" if since is None else f"/backup?from={int(since)}"
        return self._send("GET", where).body

    def health(self) -> Health:
        return self._condition("/health")

    def ready(self) -> Health:
        return self._condition("/ready")

    def end_session(self) -> None:
        """Give the token back. Answers the same whether or not the node held it."""
        with self._lock:
            token = self._token
            self._token = None
        if token is not None:
            self._request("DELETE", "/session", None, None, f"Bearer {token}")

    # ---- paths ----

    def _condition(self, where: str) -> Health:
        # 503 on these two routes is an ANSWER: the node replied to the question.
        answer = self._send("GET", where, allow=(503,))
        return read_health(_json(answer.body))

    # ---- the session ----

    def _send(
        self,
        method: str,
        where: str,
        body: bytes | None = None,
        media: str | None = None,
        allow: tuple[int, ...] = (),
    ) -> _Answer:
        answer = self._request(method, where, body, media, self._authorization())
        if answer.status == 401 and self._discard():
            # A 429 is NOT retried here and must not be: this node applies a
            # per-user sign-in limiter that §5.2 does not enumerate, and it locks
            # out a VALID password after earlier failures (Q-PY-008). Re-opening
            # a session against it turns one refusal into a lockout.
            # Held a token and it stopped working — one of four ways, and a
            # client cannot tell them apart. Sign in again and retry ONCE.
            answer = self._request(method, where, body, media, self._authorization())
        if answer.status in allow or 200 <= answer.status < 300:
            return answer
        # A 307 is an instruction and not a refusal, but a client with no
        # routing behaviour must report it and stop rather than retry this node.
        # The address is in `Location`, because a redirect whose target a client
        # must parse out of prose is not a redirect.
        raise HTTPError(answer.status, _message(answer.body), answer.location)

    def _authorization(self) -> str | None:
        if self._user is None:
            return None
        with self._lock:
            if self._token is not None:
                return f"Bearer {self._token}"
            # Asked once. A store with no session to open will not grow one, and
            # asking per request costs an Argon2id verification per request —
            # the exact expense the token exists to remove — while walking into
            # this node's per-user sign-in limiter (Q-PY-008).
            if self._sessionless:
                return self._basic()
        token = self._open_session()
        return f"Bearer {token}" if token else self._basic()

    def _open_session(self) -> str | None:
        answer = self._request("POST", "/session", None, None, self._basic())
        if answer.status == 200:
            token = _json(answer.body)["token"]
            with self._lock:
                self._token = token
            return token
        if answer.status == 401:
            # An open store has no session to open, and this is not a failure to
            # retry: a token cut from the ABSENCE of a credential would still work
            # after the first DEFINE USER closed the store.
            with self._lock:
                self._sessionless = True
            return None
        raise HTTPError(answer.status, _message(answer.body))

    def _discard(self) -> bool:
        with self._lock:
            held = self._token is not None
            self._token = None
        return held

    def _basic(self) -> str:
        raw = f"{self._user}:{self._password or ''}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _request(
        self, method: str, where: str, body: bytes | None, media: str | None, authorization: str | None
    ) -> _Answer:
        headers = {}
        if media is not None:
            headers["Content-Type"] = media
        if authorization is not None:
            headers["Authorization"] = authorization
        host, _, port = self.address.rpartition(":")
        connection = http.client.HTTPConnection(host, int(port))
        try:
            connection.request(method, where, body, headers)
            response = connection.getresponse()
            # §5.3 requires every response on this surface to declare its
            # length and forbids `Transfer-Encoding: chunked` on any route,
            # naming `GET /backup` as the one that must still declare it. This
            # node chunks exactly that route (Q-PY-009). The framing is read
            # rather than refused: §5.3's refusal is for a framing a client does
            # not RECOGNISE, and chunked is recognised — the standard library
            # de-chunks it transparently. Refusing a recognised framing would be
            # this client inventing a stricter rule than the document states and
            # making a working route unusable.
            return _Answer(
                response.status, response.read(), response.getheader("Location", "") or ""
            )
        finally:
            connection.close()


def _json(body: bytes) -> dict:
    # Escaping is real and must not be hand-parsed: these strings carry arbitrary
    # user input through JSON escaping, and a reader that scans for the text
    # between quotation marks returns a truncated string and reports success.
    try:
        return json.loads(body)
    except ValueError as why:
        raise Malformed(f"the answer is not JSON: {why}") from why


def _message(body: bytes) -> str:
    try:
        return json.loads(body).get("error", "")
    except ValueError:
        return body.decode("utf-8", "replace")
