"""A vault — vault contract 1.0 (``spec/vault-v1.md`` in the protocol repository).

Two halves. The store's acts — :func:`vault_status`, :func:`unseal`, :func:`seal`,
:func:`change_passphrase` — go in a frame of their own (protocol §3.14), so a
passphrase is a field and never a statement: statement text is what a console
keeps and a client logs on failure. A :class:`Vault` then lists, reveals, writes
and shares the records of one vault with statements whose every id and value is
bound, and acts on that vault alone when it carries its own passphrase.

What a vault promises, said as narrowly as it is true: the stored bytes, backups
and replicas are ciphertext; a running node that is unsealed can decrypt, because
it must to answer a reveal. An unseal lasts the node's period (ten minutes unless
it was started otherwise) and then closes by itself. A refusal after a run of
wrong passphrases means **wait**, and is not retried here.

A passphrase given to these functions is sent and dropped. It is in no error this
module raises and no ``repr`` of anything it returns; the node never quotes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence, Union

from ._vault_frame import Custody, SealState, VaultStatus, frame_body
from .connection import Connection
from .errors import Malformed
from .outcome import Done, ValueOutcome
from .query import check_name
from .value import Array, Bytes, Integer, NoneValue, Object, Text, Value

__all__ = [
    "Custody",
    "NotAVaultArgument",
    "Page",
    "SealState",
    "Vault",
    "VaultStatus",
    "change_passphrase",
    "seal",
    "unseal",
    "vault_audit",
    "vault_status",
]

#: The most ids one listing may ask for (§3.1).
MOST_IDS = 10_000

IdLike = Union[str, int, Value]
Rendered = tuple[str, dict[str, Value]]


class NotAVaultArgument(ValueError):
    """An argument the vault contract refuses before sending (§6): a listing
    limit outside 1-10000 (``bad-limit``), a write with no fields (``no-fields``)."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def vault_status(conn: Connection) -> VaultStatus:
    """Whether the node can open secrets with the store's key, and until when."""
    return VaultStatus.read(conn._vault(lambda creds: frame_body(creds, "status")))


def unseal(conn: Connection, passphrase: str) -> VaultStatus:
    """Present the store's passphrase. The first one ever presented becomes the
    passphrase, and the answer says so with ``initialised``."""
    return VaultStatus.read(
        conn._vault(lambda creds: frame_body(creds, "unseal", passphrase=passphrase))
    )


def seal(conn: Connection) -> VaultStatus:
    """Drop the store's key: nothing in its custody can be revealed until the next unseal."""
    return VaultStatus.read(conn._vault(lambda creds: frame_body(creds, "seal")))


def change_passphrase(conn: Connection, current: str, new: str) -> VaultStatus:
    """Wrap the store's key under a new passphrase. No secret is re-encrypted,
    and a backup taken before the change still opens with the old one."""
    return VaultStatus.read(
        conn._vault(lambda creds: frame_body(creds, "change", current=current, new=new))
    )


def audit_statement(namespace: str, database: str, by: str | None = None) -> Rendered:
    """``INFO FOR AUDIT [BY actor]`` (§3.5), with the actor checked and written in."""
    tenancy = _tenancy(namespace, database)
    if by is None:
        return f"{tenancy}INFO FOR AUDIT;", {}
    return f"{tenancy}INFO FOR AUDIT BY {check_name('an actor', by)};", {}


def vault_audit(conn: Connection, namespace: str, database: str, by: str | None = None) -> list[Value]:
    """The store's trail of vault reads, optionally one user's. Answered only to
    a caller who administers the whole store."""
    report = _value(conn, *audit_statement(namespace, database, by))
    entries = report.fields.get("audit") if isinstance(report, Object) else None
    if not isinstance(entries, Array):
        raise Malformed("an audit answer holds an array")
    return list(entries.items)


@dataclass(frozen=True)
class Page:
    """One page of a vault's record ids, in key order; ``next`` is ``None`` on the last."""

    ids: list[Value]
    next: Value | None


class _Statements:
    """The statements a vault handle sends (§3), rendered in one place. Only
    checked names are written into the text; fields are written **quoted**."""

    def __init__(self, namespace: str, database: str, vault: str) -> None:
        self._tenancy = _tenancy(namespace, database)
        self._vault = check_name("a vault", vault)

    def list(self, after: Value | None, limit: int | None) -> Rendered:
        clauses, given = "", {}
        if after is not None:
            clauses += f" AFTER {self._vault}:$after"
            given["after"] = after
        if limit is not None:
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= MOST_IDS
            ):
                raise NotAVaultArgument(
                    "bad-limit", "a listing asks for 1 to 10000 ids"
                )
            clauses += f" LIMIT {limit}"
        return f"{self._tenancy}INFO FOR VAULT {self._vault} RECORDS{clauses};", given

    def reveal(self, record: Value, fields: Sequence[str]) -> Rendered:
        names = _sorted_fields(fields)
        which = ", ".join(f"'{name}'" for name in names) if names else "*"
        return f"{self._tenancy}REVEAL {which} FROM {self._vault}:$id;", {"id": record}

    def write(self, record: Value, fields: Mapping[str, Value]) -> Rendered:
        if not fields:
            raise NotAVaultArgument("no-fields", "a write sets at least one field")
        given: dict[str, Value] = {"id": record}
        pairs = []
        for index, name in enumerate(_sorted_fields(fields)):
            pairs.append(f"'{name}': $f{index}")
            given[f"f{index}"] = fields[name]
        return (
            f"{self._tenancy}UPSERT {self._vault}:$id MERGE {{ {', '.join(pairs)} }};",
            given,
        )

    def recipients(self, record: Value) -> Rendered:
        return f"{self._tenancy}INFO FOR RECIPIENTS OF {self._vault}:$id;", {
            "id": record
        }

    def add_recipient(self, record: Value, name: str, key: bytes) -> Rendered:
        return (
            f"{self._tenancy}ADD RECIPIENT $name TO {self._vault}:$id KEY $key;",
            {"id": record, "name": Text(name), "key": Bytes(key)},
        )

    def remove_recipient(self, record: Value, name: str) -> Rendered:
        return (
            f"{self._tenancy}REMOVE RECIPIENT $name FROM {self._vault}:$id;",
            {"id": record, "name": Text(name)},
        )


class Vault:
    """One vault in a namespace and database, over a connection the caller holds.

    Every call sends its own ``USE``, so a connection that reconnected underneath
    cannot read another database. The three names are checked here, before
    anything is sent.
    """

    def __init__(self, conn: Connection, namespace: str, database: str, vault: str) -> None:
        self._conn = conn
        self._statements = _Statements(namespace, database, vault)
        self._place = (namespace, database, vault)

    def status(self) -> VaultStatus:
        """This vault's seal status, with ``custody`` saying what opens it; for a
        vault in the store's custody the state is the store's."""
        return self._act("status")

    def unseal(self, passphrase: str) -> VaultStatus:
        """Unseal this vault with its own passphrase, for the node's period. A
        vault in the store's custody is refused rather than unsealed through the
        store, which would open every other vault the store holds."""
        return self._act("unseal", passphrase=passphrase)

    def seal(self) -> VaultStatus:
        """Seal this vault; the store and every other vault stay as they were."""
        return self._act("seal")

    def change_passphrase(self, current: str, new: str) -> VaultStatus:
        """Wrap this vault's key under a new passphrase; no secret is re-encrypted."""
        return self._act("change", current=current, new=new)

    def list(self, after: Value | None = None, limit: int | None = None) -> Page:
        """One page of ids after ``after`` (the previous page's ``next``), at most
        ``limit`` of them (1 to 10000; the node's own 1000 when ``None``)."""
        report = self._value(self._statements.list(after, limit))
        ids = report.fields.get("records") if isinstance(report, Object) else None
        if not isinstance(ids, Array):
            raise Malformed("a listing holds an array of ids")
        following = report.fields.get("next")
        return Page(
            list(ids.items),
            None
            if following is None or isinstance(following, NoneValue)
            else following,
        )

    def reveal(self, record: IdLike, fields: Sequence[str] = ()) -> dict[str, Value]:
        """The named secret fields of one record, or every secret field when none
        are named. Recorded by the node before it answers; this module keeps no copy."""
        revealed = self._value(self._statements.reveal(_id(record), fields))
        if not isinstance(revealed, Object):
            raise Malformed("a reveal answers an object")
        return dict(revealed.fields)

    def write(self, record: IdLike, fields: Mapping[str, Value]) -> None:
        """Set these fields, creating the record when absent and keeping every
        other field and every recipient."""
        self._conn.execute(*self._statements.write(_id(record), fields))

    def recipients(self, record: IdLike) -> dict[str, bytes]:
        """Who may one day open this record: name → the key material they hold."""
        report = self._value(self._statements.recipients(_id(record)))
        held = report.fields.get("recipients") if isinstance(report, Object) else None
        if not isinstance(held, Object) or not all(
            isinstance(k, Bytes) for k in held.fields.values()
        ):
            raise Malformed("recipients are names to bytes")
        return {
            name: key.value
            for name, key in held.fields.items()
            if isinstance(key, Bytes)
        }

    def add_recipient(self, record: IdLike, name: str, key: bytes) -> None:
        """Add a recipient; a name already present is refused, never replaced."""
        self._conn.execute(*self._statements.add_recipient(_id(record), name, key))

    def remove_recipient(self, record: IdLike, name: str) -> None:
        """Remove a recipient; one that is not there is refused, never answered ``ok``."""
        self._conn.execute(*self._statements.remove_recipient(_id(record), name))

    def _act(self, act: str, **fields: str) -> VaultStatus:
        place = self._place
        return VaultStatus.read(
            self._conn._vault(
                lambda creds: frame_body(creds, act, place=place, **fields)
            )
        )

    def _value(self, rendered: Rendered) -> Value:
        return _value(self._conn, *rendered)


def _tenancy(namespace: str, database: str) -> str:
    check_name("a namespace", namespace)
    check_name("a database", database)
    return f"USE NAMESPACE {namespace}; USE DATABASE {database}; "


def _sorted_fields(fields: Sequence[str] | Mapping[str, Value]) -> list[str]:
    return sorted(
        (check_name("a field", name) for name in fields), key=lambda name: name.encode()
    )


def _id(record: IdLike) -> Value:
    if isinstance(record, Value):
        return record
    if isinstance(record, bool):
        raise TypeError("a record id is a string, an integer or a value")
    if isinstance(record, str):
        return Text(record)
    if isinstance(record, int):
        return Integer(record)
    raise TypeError("a record id is a string, an integer or a value")


def _value(conn: Connection, script: str, given: dict[str, Value]) -> Value:
    reply = conn.execute(script, given)
    answered = reply.outcomes[-1] if reply.outcomes else None
    if isinstance(answered, ValueOutcome):
        return answered.value
    if isinstance(answered, Done):
        raise Malformed("a vault read answered no value")
    raise Malformed(f"a vault statement answered {type(answered).__name__}")
