"""The vault corpus (``vault-v1.json``) byte for byte, the status a node answers
with, and — with ``TESSARIDB_TEST_NODE`` set — the whole vault contract against a
running node (vault contract §7), with every error scanned for the passphrase."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tessaridb  # noqa: E402
from corpus import read_corpus  # noqa: E402
from tessaridb import (  # noqa: E402
    Bool,
    BuilderError,
    Bytes,
    Custody,
    Datetime,
    Duration,
    Integer,
    NotAVaultArgument,
    Object,
    Refused,
    SealState,
    Text,
    Vault,
    VaultStatus,
)
from tessaridb._vault_frame import frame_body  # noqa: E402
from tessaridb.vault import _Statements, audit_statement  # noqa: E402


def parameter(node: dict):
    ((kind, held),) = node.items()
    if kind == "string":
        return Text(held)
    if kind == "integer":
        return Integer(int(held))
    if kind == "bytes":
        return Bytes(bytes.fromhex(held))
    raise AssertionError(f"a parameter kind the corpus does not define: {kind}")


def rendered(build: dict):
    ((kind, fields),) = build.items()
    namespace, database = fields["namespace"], fields["database"]
    if kind == "audit":
        return audit_statement(namespace, database, fields.get("actor"))
    s = _Statements(namespace, database, fields["vault"])
    if kind == "list":
        after = parameter(fields["after"]) if "after" in fields else None
        return s.list(after, fields.get("limit"))
    record = parameter(fields["id"])
    if kind == "reveal":
        return s.reveal(record, fields.get("fields", []))
    if kind == "write":
        return s.write(
            record, {name: parameter(v) for name, v in fields["fields"].items()}
        )
    if kind == "recipients":
        return s.recipients(record)
    if kind == "add_recipient":
        return s.add_recipient(record, fields["name"], bytes.fromhex(fields["key"]))
    if kind == "remove_recipient":
        return s.remove_recipient(record, fields["name"])
    raise AssertionError(f"a statement the corpus does not define: {kind}")


class Corpus(unittest.TestCase):
    corpus = read_corpus("vault-v1.json")

    def test_every_frame_is_the_corpus_bytes(self) -> None:
        frames = self.corpus["frames"]
        self.assertEqual(len(frames), 11, "the corpus shrank")
        for case in frames:
            build = case["build"]
            credentials = build.get("credentials")
            body = frame_body(
                (credentials["name"], credentials["password"]) if credentials else None,
                build["act"],
                passphrase=build.get("passphrase"),
                current=build.get("current"),
                new=build.get("new"),
                place=(
                    (
                        build["vault"]["namespace"],
                        build["vault"]["database"],
                        build["vault"]["vault"],
                    )
                    if "vault" in build
                    else None
                ),
            )
            self.assertEqual(body.hex(), case["body_hex"], case["name"])

    def test_every_statement_renders_or_is_refused_as_the_corpus_says(self) -> None:
        cases = self.corpus["statements"]
        self.assertEqual(len(cases), 19, "the corpus shrank")
        for case in cases:
            if "refused" in case:
                with self.assertRaises(
                    (BuilderError, NotAVaultArgument), msg=case["name"]
                ) as caught:
                    rendered(case["build"])
                reason = case["refused"]["reason"]
                if isinstance(caught.exception, BuilderError):
                    self.assertEqual(caught.exception.reason, reason, case["name"])
                    self.assertEqual(
                        caught.exception.what, case["refused"]["what"], case["name"]
                    )
                else:
                    self.assertEqual(caught.exception.reason, reason, case["name"])
                continue
            script, given = rendered(case["build"])
            self.assertEqual(script, case["script"], case["name"])
            self.assertEqual(
                given,
                {name: parameter(value) for name, value in case["parameters"].items()},
                case["name"],
            )


def status_value(state: str, **more) -> Object:
    fields = {"state": Text(state), "unseal_for": Duration(600, 0)}
    fields.update(more)
    return Object(fields)


class Status(unittest.TestCase):
    def test_the_three_states_and_the_instant_only_when_unsealed(self) -> None:
        self.assertEqual(
            VaultStatus.read(status_value("sealed")).state, SealState.SEALED
        )
        opened = VaultStatus.read(
            status_value(
                "unsealed", seals_at=Datetime(1_790_000_000, 0), initialised=Bool(True)
            )
        )
        self.assertEqual(opened.state, SealState.UNSEALED)
        self.assertEqual(
            opened.seals_at, datetime.fromtimestamp(1_790_000_000, timezone.utc)
        )
        self.assertEqual(opened.unseal_for, timedelta(seconds=600))
        self.assertTrue(opened.initialised)
        self.assertIsNone(opened.custody)
        with self.assertRaises(tessaridb.Malformed):
            VaultStatus.read(status_value("ajar"))

    def test_custody_is_a_closed_set(self) -> None:
        self.assertEqual(
            VaultStatus.read(status_value("sealed", custody=Text("own"))).custody,
            Custody.OWN,
        )
        self.assertEqual(
            VaultStatus.read(status_value("sealed", custody=Text("store"))).custody,
            Custody.STORE,
        )
        with self.assertRaises(tessaridb.Malformed):
            VaultStatus.read(status_value("sealed", custody=Text("shared")))


class NoPassphraseIsEverShown(unittest.TestCase):
    def test_a_node_too_old_is_refused_before_sending(self) -> None:
        refused = tessaridb.NodeTooOld(1, 2)
        self.assertIn("2", str(refused))


PASSPHRASE = "an operator passphrase 4b71"
NEXT = "the next passphrase 9c02"
TEAM = "the team passphrase 5d13"
PLANTED = "correct-horse-battery-staple-9f2b"


def node(test: unittest.TestCase) -> str:
    address = os.environ.get("TESSARIDB_TEST_NODE")
    if not address:
        test.skipTest("set TESSARIDB_TEST_NODE=<host:port> to run the live tests")
    return address


def shown(error: BaseException) -> str:
    return f"{error} {error!r}"


class VaultAgainstANode(unittest.TestCase):
    """Against a node with an empty store: it sets the store's first passphrase."""

    def test_the_whole_vault_contract(self) -> None:
        with tessaridb.connect(node(self)) as conn:
            first = tessaridb.unseal(conn, PASSPHRASE)
            if not first.initialised:
                self.skipTest(
                    "the node's store already has a passphrase; run against an empty one"
                )
            self.assertEqual(first.state, SealState.UNSEALED)
            self.assertIsNotNone(first.seals_at)
            conn.execute(
                "DEFINE NAMESPACE app; USE NAMESPACE app; DEFINE DATABASE main; USE DATABASE main; "
                "DEFINE VAULT team; DEFINE FIELD 'password' ON team TYPE string SECRET; "
                "DEFINE FIELD login ON team TYPE string;"
            )
            vault = Vault(conn, "app", "main", "team")
            vault.write("github", {"password": Text(PLANTED), "login": Text("boog")})
            vault.write("gitlab", {"password": Text("second")})
            vault.write("github", {"password": Text(PLANTED)})

            page = vault.list(limit=1)
            self.assertEqual(page.ids, [Text("github")])
            page = vault.list(after=page.next, limit=1)
            self.assertEqual(page.ids, [Text("gitlab")])
            last = vault.list(after=page.next, limit=1)
            self.assertEqual((last.ids, last.next), ([], None))

            revealed = vault.reveal("github", ["password"])
            self.assertEqual(revealed, {"password": Text(PLANTED)})
            self.assertEqual(vault.reveal("github"), revealed)

            vault.add_recipient("github", "bob", b"\x01\x02\x03")
            self.assertEqual(vault.recipients("github"), {"bob": b"\x01\x02\x03"})
            vault.remove_recipient("github", "bob")
            self.assertEqual(vault.recipients("github"), {})
            with self.assertRaises(Refused):
                vault.remove_recipient("github", "bob")

            trail = tessaridb.vault_audit(conn, "app", "main")
            self.assertGreaterEqual(len(trail), 2)
            self.assertNotIn(PLANTED, repr(trail))

            with self.assertRaises(Refused) as wrong:
                tessaridb.change_passphrase(conn, "not it", NEXT)
            self.assertNotIn(NEXT, shown(wrong.exception))
            self.assertNotIn("not it", shown(wrong.exception))
            tessaridb.change_passphrase(conn, PASSPHRASE, NEXT)
            self.assertEqual(tessaridb.seal(conn).state, SealState.SEALED)
            with self.assertRaises(Refused) as old:
                tessaridb.unseal(conn, PASSPHRASE)
            self.assertNotIn(PASSPHRASE, shown(old.exception))
            self.assertEqual(tessaridb.unseal(conn, NEXT).state, SealState.UNSEALED)
            self.assertEqual(
                vault.reveal("github", ["password"]), {"password": Text(PLANTED)}
            )

            # A vault with its own passphrase: the store's opens nothing in it.
            conn.execute(
                f"USE NAMESPACE app; USE DATABASE main; DEFINE VAULT own PASSPHRASE '{TEAM}'; "
                "DEFINE FIELD token ON own TYPE string SECRET;"
            )
            own = Vault(conn, "app", "main", "own")
            status = own.status()
            self.assertEqual(
                (status.custody, status.state), (Custody.OWN, SealState.UNSEALED)
            )
            own.write("github", {"token": Text(PLANTED)})
            self.assertEqual(own.seal().state, SealState.SEALED)
            with self.assertRaises(Refused) as refused:
                own.unseal(NEXT)
            self.assertNotIn(NEXT, shown(refused.exception))
            self.assertEqual(own.unseal(TEAM).state, SealState.UNSEALED)
            own.change_passphrase(TEAM, "the next team one")
            self.assertEqual(own.reveal("github", ["token"]), {"token": Text(PLANTED)})
            self.assertEqual(vault.status().custody, Custody.STORE)
            with self.assertRaises(Refused):
                vault.unseal(NEXT)


if __name__ == "__main__":
    unittest.main()
