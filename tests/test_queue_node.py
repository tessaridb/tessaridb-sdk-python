"""The queue worker against a running node (queue contract §7).

Run with ``TESSARIDB_TEST_NODE=127.0.0.1:47915``; skipped without it. The one
real wait is a release's ``NOT BEFORE``, an instant the NODE compares with its
own clock.
"""

import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import tessaridb  # noqa: E402
from tessaridb import Integer, Leave, Object, Queue, Records, Text  # noqa: E402
from tessaridb.query import compare  # noqa: E402

USE = "USE NAMESPACE pyqueue; USE DATABASE app;"


def node(test: unittest.TestCase) -> str:
    address = os.environ.get("TESSARIDB_TEST_NODE")
    if not address:
        test.skipTest("set TESSARIDB_TEST_NODE=<host:port> to run the live tests")
    return address


def queue(address: str, stem: str, count: int, clauses: str) -> str:
    """A fresh queue named after ``stem``, so a rerun never meets the last run's work."""
    name = f"{stem}_{time.time_ns()}"
    with tessaridb.connect(address) as conn:
        conn.execute(
            "DEFINE NAMESPACE IF NOT EXISTS pyqueue; USE NAMESPACE pyqueue; "
            "DEFINE DATABASE IF NOT EXISTS app; USE DATABASE app; "
            f"DEFINE QUEUE {name} {clauses};"
        )
        creates = " ".join(
            f"CREATE {name}:{n} = {{ n: {n}, kind: '{'image' if n % 2 == 0 else 'video'}' }};"
            for n in range(1, count + 1)
        )
        conn.execute(f"{USE} {creates}")
    return name


def n_of(job: tessaridb.Job) -> int:
    assert isinstance(job.value, Object)
    held = job.value.fields["n"]
    assert isinstance(held, Integer)
    return held.value


def left(conn: tessaridb.Connection, name: str) -> int:
    answered = conn.execute(f"{USE} SELECT * FROM {name};").outcomes[-1]
    assert isinstance(answered, Records)
    return len(answered.rows)


class QueueAgainstANode(unittest.TestCase):
    def test_auto_takes_every_record_once_and_leaves_the_queue_empty(self) -> None:
        address = node(self)
        name = queue(address, "auto_jobs", 12, "TIMEOUT 30s")
        conn = tessaridb.connect(address)
        self.addCleanup(conn.close)
        worker = Queue(conn, "pyqueue", "app", name, "resizer", batch=5)
        seen: list[int] = []

        def handle(job: tessaridb.Job) -> None:
            seen.append(n_of(job))
            if len(seen) == 12:
                worker.stop()

        worker.run_auto(handle)
        self.assertEqual(seen, list(range(1, 13)))
        self.assertEqual(left(conn, name), 0)

    def test_a_failing_handler_sees_the_same_record_again_one_attempt_later(self) -> None:
        address = node(self)
        name = queue(address, "flaky_jobs", 2, "TIMEOUT 30s ATTEMPTS 3")
        conn = tessaridb.connect(address)
        self.addCleanup(conn.close)
        worker = Queue(conn, "pyqueue", "app", name, "resizer")
        seen: list[tuple[int, int]] = []

        def handle(job: tessaridb.Job) -> None:
            seen.append((n_of(job), job.attempts))
            if len(seen) == 3:
                worker.stop()
            if n_of(job) == 1 and job.attempts == 1:
                raise RuntimeError("the first attempt fails once")

        worker.run_auto(handle)
        self.assertEqual(seen, [(1, 1), (2, 1), (1, 2)])

    def test_a_condition_takes_only_the_records_that_match(self) -> None:
        address = node(self)
        name = queue(address, "mixed_jobs", 6, "TIMEOUT 30s")
        conn = tessaridb.connect(address)
        self.addCleanup(conn.close)
        worker = Queue(conn, "pyqueue", "app", name, "resizer")
        taken = worker.claim(10, compare("kind", "=", Text("image")))
        self.assertEqual([n_of(job) for job in taken], [2, 4, 6])
        for job in taken:
            worker.done(job)
        self.assertEqual(left(conn, name), 3)

    def test_a_release_with_a_delay_hands_the_record_out_only_after_it(self) -> None:
        address = node(self)
        name = queue(address, "later_jobs", 1, "TIMEOUT 30s NOT BEFORE due")
        conn = tessaridb.connect(address)
        self.addCleanup(conn.close)
        worker = Queue(conn, "pyqueue", "app", name, "resizer")
        (job,) = worker.claim()
        worker.release(job, after=0.4)
        self.assertEqual(worker.claim(), [], "handed out before its delay")
        time.sleep(0.6)
        self.assertEqual([(n_of(again), again.attempts) for again in worker.claim()], [(1, 2)])

    def test_stop_hands_back_what_the_handler_never_saw(self) -> None:
        address = node(self)
        name = queue(address, "stopped_jobs", 3, "TIMEOUT 30s")
        conn = tessaridb.connect(address)
        self.addCleanup(conn.close)
        worker = Queue(conn, "pyqueue", "app", name, "resizer", batch=3)

        def handle(job: tessaridb.Job) -> Leave:
            worker.stop()
            return Leave()

        worker.run_manual(handle)
        other = Queue(conn, "pyqueue", "app", name, "other")
        self.assertEqual([n_of(job) for job in other.claim(3)], [2, 3])


if __name__ == "__main__":
    unittest.main()
