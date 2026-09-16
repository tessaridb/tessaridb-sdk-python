"""What `GET /health` and `GET /ready` answer — §5.4.

**Three variants with distinct field sets, not one record with optional fields.**
``leaving`` carries no ``committed``, and a client that modelled this as one
record would offer a caller a commit position that is absent for a reason the
type cannot express.

**`503` on these two routes is an ANSWER, not a transport failure** — the node
has replied to the question it was asked. A status this build does not know is
refused rather than mapped onto the nearest one it does.

**The two routes are not synonyms and neither is implemented in terms of the
other.** They answer identically on a well node and diverge during a staged
shutdown, where ``/ready`` reports ``leaving`` while ``/health`` still reports
``ok`` — and that window is the whole reason both exist. A supervisor reads *not
ready* as **stop sending traffic here** and *not healthy* as **restart this**, so
a client that reported one for the other inverts an operational decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import Malformed

__all__ = ["Health", "Healthy", "Unwell", "Leaving", "read_health"]


class Health:
    pass


@dataclass(frozen=True)
class Healthy(Health):
    committed: int


@dataclass(frozen=True)
class Unwell(Health):
    committed: int
    background_errors: int
    complaint: str


@dataclass(frozen=True)
class Leaving(Health):
    """A node on its way out. It has no commit position to report."""


def read_health(body: dict) -> Health:
    status = body.get("status")
    if status == "ok":
        return Healthy(int(body["committed"]))
    if status == "unwell":
        return Unwell(
            int(body["committed"]),
            int(body.get("background_errors", 0)),
            body.get("complaint", ""),
        )
    if status == "leaving":
        return Leaving()
    raise Malformed(f"{status!r} is not a health this build knows, and guessing would be worse")
