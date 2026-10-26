"""The container calls' statements, as cache contract 1.1 §2a writes them."""

from __future__ import annotations

from typing import Mapping, Sequence

from .value import Float, Integer, Text, Value

Rendered = tuple[str, dict[str, Value]]


class _ContainerStatements:
    """The §2a statements, rendered in one place; mixed into ``_Statements``."""

    _tenancy: str
    _space: str

    def _bound(self, statement: str, key: str) -> Rendered:
        return f"{self._tenancy}{statement}", {"k": Text(key)}

    def _numbered(
        self, statement: str, key: str, prefix: str, values: Sequence[Value]
    ) -> Rendered:
        given: dict[str, Value] = {"k": Text(key)}
        names = []
        for at, value in enumerate(values):
            given[f"{prefix}{at}"] = value
            names.append(f"${prefix}{at}")
        return f"{self._tenancy}{statement.format(list=', '.join(names))}", given

    def _paired(
        self,
        statement: str,
        key: str,
        names: tuple[str, str],
        pairs: Sequence[tuple[Value, Value]],
    ) -> Rendered:
        left, right = names
        given: dict[str, Value] = {"k": Text(key)}
        written = []
        for at, (name, value) in enumerate(pairs):
            given[f"{left}{at}"] = name
            given[f"{right}{at}"] = value
            written.append(f"${left}{at} = ${right}{at}")
        return f"{self._tenancy}{statement.format(list=', '.join(written))}", given

    def list_push(self, key: str, values: Sequence[Value], front: bool) -> Rendered:
        end = "FRONT" if front else "BACK"
        return self._numbered(
            f"LIST PUSH {self._space}:$k {end} {{list}};", key, "v", values
        )

    def list_pop(self, key: str, front: bool, count: int | None) -> Rendered:
        end = "FRONT" if front else "BACK"
        if count is None:
            return self._bound(f"LIST POP {self._space}:$k {end};", key)
        script, given = self._bound(f"LIST POP {self._space}:$k {end} LIMIT $n;", key)
        given["n"] = Integer(count)
        return script, given

    def list_range(self, key: str, start: int, count: int) -> Rendered:
        script, given = self._bound(
            f"RETURN array::slice(GET {self._space}:$k, $i, $n);", key
        )
        given["i"], given["n"] = Integer(start), Integer(count)
        return script, given

    def list_at(self, key: str, index: int) -> Rendered:
        script, given = self._bound(f"RETURN array::at(GET {self._space}:$k, $i);", key)
        given["i"] = Integer(index)
        return script, given

    def list_len(self, key: str) -> Rendered:
        return self._bound(f"RETURN array::len(GET {self._space}:$k);", key)

    def list_remove(self, key: str, value: Value, count: int | None) -> Rendered:
        limit = "" if count is None else " LIMIT $n"
        script, given = self._bound(f"LIST REMOVE {self._space}:$k $v{limit};", key)
        given["v"] = value
        if count is not None:
            given["n"] = Integer(count)
        return script, given

    def list_trim(self, key: str, start: int, stop: int) -> Rendered:
        script, given = self._bound(f"LIST TRIM {self._space}:$k FROM $i TO $j;", key)
        given["i"], given["j"] = Integer(start), Integer(stop)
        return script, given

    def hash_set(self, key: str, fields: Mapping[str, Value]) -> Rendered:
        pairs = [(Text(name), value) for name, value in fields.items()]
        return self._paired(
            f"HASH SET {self._space}:$k {{list}};", key, ("f", "v"), pairs
        )

    def hash_get(self, key: str, field: str) -> Rendered:
        script, given = self._bound(
            f"RETURN object::get(GET {self._space}:$k, $f);", key
        )
        given["f"] = Text(field)
        return script, given

    def hash_remove(self, key: str, fields: Sequence[str]) -> Rendered:
        return self._numbered(
            f"HASH REMOVE {self._space}:$k {{list}};",
            key,
            "f",
            [Text(name) for name in fields],
        )

    def hash_incr(self, key: str, field: str, by: int) -> Rendered:
        script, given = self._bound(f"HASH INCR {self._space}:$k $f BY $n;", key)
        given["f"], given["n"] = Text(field), Integer(by)
        return script, given

    def hash_len(self, key: str) -> Rendered:
        return self._bound(f"RETURN object::len(GET {self._space}:$k);", key)

    def members(self, verb: str, key: str, members: Sequence[Value]) -> Rendered:
        return self._numbered(
            f"MEMBERS {verb} {self._space}:$k {{list}};", key, "v", members
        )

    def members_has(self, key: str, member: Value) -> Rendered:
        script, given = self._bound(f"RETURN $v IN GET {self._space}:$k;", key)
        given["v"] = member
        return script, given

    def members_len(self, key: str) -> Rendered:
        return self._bound(f"RETURN set::len(GET {self._space}:$k);", key)

    def scores_add(self, key: str, scores: Mapping[str, float]) -> Rendered:
        pairs = [
            (Text(member), Float(float(score))) for member, score in scores.items()
        ]
        return self._paired(
            f"SCORES ADD {self._space}:$k {{list}};", key, ("m", "c"), pairs
        )

    def scores_incr(self, key: str, member: str, by: float) -> Rendered:
        script, given = self._bound(f"SCORES INCR {self._space}:$k $m BY $c;", key)
        given["m"], given["c"] = Text(member), Float(float(by))
        return script, given

    def scores_remove(self, key: str, members: Sequence[str]) -> Rendered:
        return self._numbered(
            f"SCORES REMOVE {self._space}:$k {{list}};",
            key,
            "m",
            [Text(name) for name in members],
        )

    def scores_of(self, function: str, key: str, member: str) -> Rendered:
        script, given = self._bound(
            f"RETURN scores::{function}(GET {self._space}:$k, $m);", key
        )
        given["m"] = Text(member)
        return script, given

    def scores_range(self, key: str, start: int, stop: int) -> Rendered:
        script, given = self._bound(
            f"RETURN scores::range(GET {self._space}:$k, $i, $j);", key
        )
        given["i"], given["j"] = Integer(start), Integer(stop)
        return script, given

    def scores_between(
        self, key: str, low: float | None, high: float | None
    ) -> Rendered:
        # An absent bound is written NONE in its place (§2a).
        low_text = "NONE" if low is None else "$lo"
        high_text = "NONE" if high is None else "$hi"
        script, given = self._bound(
            f"RETURN scores::between(GET {self._space}:$k, {low_text}, {high_text});",
            key,
        )
        if low is not None:
            given["lo"] = Float(float(low))
        if high is not None:
            given["hi"] = Float(float(high))
        return script, given
