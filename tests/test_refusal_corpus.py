"""The Refusal body of §3.6 — every vector in ``frames-v1.json`` under ``refusal``.

Read through the same function every refusal this client receives goes through,
so a reader that agreed with itself and not with the document fails here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import read_corpus  # noqa: E402

from tessaridb import RefusalClass  # noqa: E402
from tessaridb import _frames as frames  # noqa: E402


class RefusalBodies(unittest.TestCase):
    def test_every_vector_reads_as_its_class_and_message(self) -> None:
        cases = read_corpus("frames-v1.json")["refusal"]
        self.assertGreaterEqual(len(cases), 12, "the corpus holds every case it should")
        for case in cases:
            with self.subTest(case["name"]):
                cls, message = frames.read_refusal(bytes.fromhex(case["body_hex"]))
                want = case["decoded"]
                self.assertEqual(cls.value if cls else None, want["class"])
                self.assertEqual(message, want["message"])

    def test_a_word_this_client_does_not_know_is_unknown(self) -> None:
        self.assertIs(RefusalClass.from_word("a class from the future"), RefusalClass.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
