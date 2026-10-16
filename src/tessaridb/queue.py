"""Taking work from a queue — queue contract 1.0 (``spec/queue-v1.md`` in the
protocol repository).

A :class:`Queue` claims records from a queue the store holds, as one named
worker, and settles each one: :meth:`Queue.done` deletes it,
:meth:`Queue.release` hands it back, now or not before a delay. A worker that
dies says nothing — its hold lapses after the queue's ``TIMEOUT`` and the next
claim takes the record — so the work is at least once; key an outside effect on
the queue name and :attr:`Job.identity`.

- :meth:`Queue.run_auto`: returning marks the record done, raising hands it back
  at once. The attempt was counted at the claim, so a record that keeps failing
  reaches the queue's ``ATTEMPTS`` (and its dead letter, if it declares one).
- :meth:`Queue.run_manual`: the handler returns :class:`Finish`,
  :class:`Release` or :class:`~tessaridb.Leave`.

The queue itself is declared in the store (``DEFINE QUEUE``), never by this
class. The loop is synchronous, as every call in this client is; run it in a
thread of its own and call :meth:`Queue.stop` from another.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Callable, Iterable

from .connection import Connection
from .consumer import Leave
from .errors import Refused, RefusalClass
from .outcome import Records
from .query import Binder, BuilderError, Filter, check_name
from .value import Integer, Object, Value

__all__ = ["Queue", "Job", "Finish", "Release", "Handled"]

#: The first wait after a claim that took nothing, in seconds (§4.5).
FIRST_WAIT = 0.05
#: The longest wait between claims that take nothing, in seconds (§4.5).
LONGEST_WAIT = 1.0
#: The most records one claim may take; the node refuses more.
CEILING = 500
#: The three fields the engine keeps on a held record, not the work's own.
_ENGINE = ("attempts", "claimed_until", "claimed_by")

_WORKER = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


@dataclass(frozen=True)
class Job:
    """One record a claim took.

    ``identity`` is the store's own spelling — ``7``, ``'a7'``, ``uuid '…'`` —
    written back unchanged when the record is settled (§2), so settle a job only
    through the queue that claimed it.
    """

    identity: str
    #: The record's own fields, without the engine's hold.
    value: Value
    #: How many times it has been handed out, 1 the first time.
    attempts: int


@dataclass(frozen=True)
class Finish:
    """Done: the record is deleted."""


@dataclass(frozen=True)
class Release:
    """Hand it back — now, or not before ``after`` seconds (the queue must
    declare a ``NOT BEFORE`` field for a delay)."""

    after: float | None = None


Handled = Finish | Release | Leave


class _Statements:
    """The statements a worker sends (§2), rendered in one place.

    The worker builds its text through nothing else, so the shared corpus
    (``queue-v1.json``) checking this class checks what actually goes out.
    """

    def __init__(self, namespace: str, database: str, queue: str, worker: str) -> None:
        for what, name in (("a namespace", namespace), ("a database", database), ("a queue", queue)):
            check_name(what, name)
        if not _WORKER.match(worker):
            raise BuilderError("not-a-name", "a worker", worker)
        # Sent with every statement: a connection that reconnected has forgotten
        # any earlier USE (§5).
        self._tenancy = f"USE NAMESPACE {namespace}; USE DATABASE {database}; "
        self._queue = queue
        self._worker = worker

    def claim(self, count: int, where: Filter | None) -> tuple[str, dict[str, Value]]:
        if not 1 <= count <= CEILING:
            raise BuilderError("not-a-count", "a count", str(count))
        binder = Binder()
        narrowed = f" WHERE {where._render(binder)}" if where is not None else ""
        script = f"{self._tenancy}USE CONSUMER '{self._worker}'; CLAIM {count} FROM {self._queue}{narrowed};"
        return script, binder.parameters

    def done(self, identity: str) -> str:
        return f"{self._tenancy}DELETE {self._queue}:{identity};"

    def release(self, identity: str, after: float | None) -> str:
        # A delay is a duration literal written from a number formatted here,
        # never from a caller's text.
        millis = int(after * 1000) if after is not None else 0
        tail = f" NOT BEFORE {millis}ms" if millis > 0 else ""
        return f"{self._tenancy}RELEASE {self._queue}:{identity} FOR CONSUMER '{self._worker}'{tail};"


class Queue:
    """The worker ``worker`` taking from ``queue`` in ``namespace``/``database``.

    Names are checked before anything is sent (§3) and refused with
    :class:`~tessaridb.BuilderError` rather than escaped. ``batch`` and
    ``where`` shape the claims the run loops make.
    """

    def __init__(
        self,
        connection: Connection,
        namespace: str,
        database: str,
        queue: str,
        worker: str,
        batch: int = 10,
        where: Filter | None = None,
    ) -> None:
        self._statements = _Statements(namespace, database, queue, worker)
        self._connection = connection
        self._batch = batch
        self._where = where
        self._stopped = threading.Event()

    def stop(self) -> None:
        """Let the running handler finish (and, in auto mode, its settlement be
        sent), hand back the rest of the batch, then stop claiming."""
        self._stopped.set()

    def claim(self, count: int = 1, where: Filter | None = None) -> list[Job]:
        """Claim up to ``count`` records, narrowed by ``where``; an empty list
        is an empty queue."""
        reply = self._connection.execute(*self._statements.claim(count, where))
        answered = reply.outcomes[-1] if reply.outcomes else None
        if not isinstance(answered, Records):
            raise ValueError(f"a claim answered {answered!r}")
        return [_job(row.identity, row.value) for row in answered.rows]

    def done(self, job: Job) -> None:
        """The work is done: delete the record."""
        self._connection.execute(self._statements.done(job.identity))

    def release(self, job: Job, after: float | None = None) -> None:
        """Hand the record back, now or not before ``after`` seconds."""
        self._connection.execute(self._statements.release(job.identity, after))

    def run_auto(self, handler: Callable[[Job], object]) -> None:
        """Call ``handler`` for each record. Returning marks it done; raising
        hands it back at once and carries on with the next."""
        while (jobs := self._next_batch()) is not None:
            for index, job in enumerate(jobs):
                try:
                    handler(job)
                except Exception:  # noqa: BLE001 — any failure hands the record back (§4.3)
                    self.release(job)
                else:
                    self.done(job)
                if self._stopped.is_set():
                    self._hand_back(jobs[index + 1 :])
                    return

    def run_manual(self, handler: Callable[[Job], Handled]) -> None:
        """Call ``handler`` for each record and do what it returns."""
        while (jobs := self._next_batch()) is not None:
            for index, job in enumerate(jobs):
                decided = handler(job)
                if isinstance(decided, Finish):
                    self.done(job)
                elif isinstance(decided, Release):
                    self.release(job, decided.after)
                if self._stopped.is_set():
                    self._hand_back(jobs[index + 1 :])
                    return

    def _hand_back(self, unseen: Iterable[Job]) -> None:
        # One statement each (§4.6): RELEASE ALL would reach live siblings.
        for job in unseen:
            self.release(job)

    def _next_batch(self) -> list[Job] | None:
        wait = FIRST_WAIT
        while not self._stopped.is_set():
            try:
                jobs = self.claim(self._batch, self._where)
            except Refused as refused:
                # Two claims raced past what the store's register sees (§6).
                if refused.refusal_class is not RefusalClass.RETRY:
                    raise
                jobs = []
            if jobs:
                return jobs
            # Woken early by stop(), so a stop during the wait is not held back.
            self._stopped.wait(wait)
            wait = min(wait * 2, LONGEST_WAIT)
        return None


def _job(identity: str, body: Value) -> Job:
    if not isinstance(body, Object):
        raise ValueError(f"a claim answered {body!r}")
    attempts = body.fields.get("attempts")
    if not isinstance(attempts, Integer):
        raise ValueError(f"a claimed record carries no attempt count: {body!r}")
    work = {name: held for name, held in body.fields.items() if name not in _ENGINE}
    return Job(identity=identity, value=Object(work), attempts=attempts.value)
