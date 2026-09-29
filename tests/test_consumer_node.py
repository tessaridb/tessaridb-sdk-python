"""The topic consumer against a running node (consumer contract §7).

Run with ``TESSARIDB_TEST_NODE=127.0.0.1:47915``; skipped without it. The waits
here are real: a group's deadline is an instant the NODE compares with its own
clock.
"""

import os
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import tessaridb  # noqa: E402
from tessaridb import Ack, BuilderError, Consumer, Integer, Leave, Object, ValueOutcome  # noqa: E402

USE = "USE NAMESPACE pyconsumer; USE DATABASE app;"


def node(test: unittest.TestCase) -> str:
    address = os.environ.get("TESSARIDB_TEST_NODE")
    if not address:
        test.skipTest("set TESSARIDB_TEST_NODE=<host:port> to run the live tests")
    return address


def topic(address: str, stem: str, count: int, group: str) -> str:
    """A fresh topic named after ``stem``, so a rerun against the same node never
    meets the last run's messages or group."""
    name = f"{stem}_{time.time_ns()}"
    group = group.replace("{topic}", name)
    with tessaridb.connect(address) as conn:
        conn.execute(
            "DEFINE NAMESPACE IF NOT EXISTS pyconsumer; USE NAMESPACE pyconsumer; "
            "DEFINE DATABASE IF NOT EXISTS app; USE DATABASE app; "
            f"DEFINE TOPIC {name};"
        )
        creates = " ".join(f"CREATE {name}:'m{n}' = {{ n: {n} }};" for n in range(1, count + 1))
        conn.execute(f"{USE} {creates} {group}")
    return name


def n_of(message: tessaridb.Message) -> int:
    assert isinstance(message.value, Object)
    held = message.value.fields["n"]
    assert isinstance(held, Integer)
    return held.value


class ConsumerAgainstANode(unittest.TestCase):
    def test_auto_hands_every_message_in_order_and_leaves_nothing_in_flight(self) -> None:
        address = node(self)
        name = topic(address, "auto_jobs", 12, "DEFINE GROUP 'workers' ON TOPIC {topic} ACK DEADLINE 30s;")
        conn = tessaridb.connect(address)
        self.addCleanup(conn.close)
        consumer = Consumer(conn, "pyconsumer", "app", name, "workers", batch=5)
        seen: list[int] = []

        def handle(message: tessaridb.Message) -> None:
            seen.append(n_of(message))
            if len(seen) == 12:
                consumer.stop()

        consumer.run_auto(handle)
        self.assertEqual(seen, list(range(1, 13)))
        reply = conn.execute(f"{USE} INFO FOR TOPIC {name};")
        report = reply.outcomes[-1]
        assert isinstance(report, ValueOutcome) and isinstance(report.value, Object)
        groups = report.value.fields["groups"]
        assert isinstance(groups, Object)
        workers = groups.fields["workers"]
        assert isinstance(workers, Object)
        self.assertEqual(workers.fields["in_flight"], Integer(0))

    def test_a_failing_handler_sees_the_same_message_again_one_delivery_later(self) -> None:
        address = node(self)
        name = topic(address, "flaky_jobs", 2, "DEFINE GROUP 'workers' ON TOPIC {topic} ACK DEADLINE 30s;")
        conn = tessaridb.connect(address)
        self.addCleanup(conn.close)
        consumer = Consumer(conn, "pyconsumer", "app", name, "workers")
        seen: list[tuple[int, int]] = []

        def handle(message: tessaridb.Message) -> None:
            seen.append((message.position, message.deliveries))
            if len(seen) == 3:
                consumer.stop()
            if message.position == 1 and message.deliveries == 1:
                raise RuntimeError("the first delivery fails once")

        consumer.run_auto(handle)
        self.assertEqual(seen, [(1, 1), (1, 2), (2, 1)])

    def test_manual_leaves_a_message_and_the_group_hands_it_out_again(self) -> None:
        address = node(self)
        name = topic(address, "left_jobs", 1, "DEFINE GROUP 'workers' ON TOPIC {topic} ACK DEADLINE 300ms;")
        conn = tessaridb.connect(address)
        self.addCleanup(conn.close)
        consumer = Consumer(conn, "pyconsumer", "app", name, "workers")
        seen: list[int] = []

        def handle(message: tessaridb.Message) -> tessaridb.Settle:
            seen.append(message.deliveries)
            if message.deliveries == 1:
                return Leave()
            consumer.stop()
            return Ack()

        watchdog = threading.Timer(10.0, consumer.stop)
        watchdog.start()
        self.addCleanup(watchdog.cancel)
        consumer.run_manual(handle)
        self.assertEqual(seen, [1, 2])


class NamesAreCheckedBeforeSending(unittest.TestCase):
    def test_a_name_that_cannot_be_written_into_a_statement_is_refused(self) -> None:
        with self.assertRaises(BuilderError):
            Consumer(None, "pyconsumer", "app", "jobs; DROP", "workers")  # type: ignore[arg-type]
        with self.assertRaises(BuilderError):
            Consumer(None, "pyconsumer", "app", "jobs", "it's")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
