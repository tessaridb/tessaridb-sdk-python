# tessaridb-client

A client for [TessariDB](https://tessaridb.com) in Python, written from the
[protocol specification](https://github.com/tessaridb/tessaridb-protocol) and
nothing else.

> **Status: early.** The value codec and the wire connection are in — the
> greeting, statements with bound parameters, answers, refusals and change
> subscriptions — and the query builder, whose every rendering is executed by a
> running node. Each is proven against the shared conformance corpora. The HTTP
> surface is not written yet.

```
pip install tessaridb-client
```

Python 3.11 or newer. Apache-2.0. **No dependencies** — the standard library
covers the protocol, and this package handles credentials, so every dependency
would be supply-chain surface inside it.

The distribution is `tessaridb-client`; the import is `tessaridb`.

## What works today

|                                                    |                                |
| -------------------------------------------------- | ------------------------------ |
| value codec — all seventeen types, both directions | **done**, 54/54 corpus vectors             |
| wire connection, greeting, statements, answers     | **done**, exercised against a running node |
| change subscription                                | **done**, exercised against a running node |
| query builder                                      | **done**, 30/30 corpus, 21 executed by a node |
| HTTP surface — objects, files, backup, health      | not yet                                    |

```python
import tessaridb

raw = tessaridb.encode(tessaridb.Integer(42))
back = tessaridb.decode(raw)      # Integer(value=42)
```

## Running a statement

```python
conn = tessaridb.connect("127.0.0.1:9080", user="app", password=os.environ["TESSARIDB_PASSWORD"])

reply = conn.execute(
    "SELECT * FROM memories WHERE session = $s LIMIT 50;",
    {"s": tessaridb.Text("abc")},
)
for row in reply.outcomes[-1].rows:
    print(row.identity, row.value)
```

**A value you pass never reaches the statement text.** It travels in the value
codec beside the script, and the server binds it after parsing — which is the
reason this transport exists. A client that formats parameters into the script
destroys the property invisibly, because the resulting script still looks
correct.

**A connection is a session.** `USE NAMESPACE prod;` is still in force in the
next statement on it, two connections are two sessions, and credentials are spent
on the first request rather than on every one: the store verifies a password with
Argon2id at the OWASP floor, so presenting one per statement pays that cost per
statement.

**A refusal is an answer, not a fault.** The store's own message is carried
through verbatim as `Refused`, the connection stays open, and a client that
mistyped a statement has not stopped being a client. The other nine error classes
are kept apart from it, because a client that collapses them into one transport
error has thrown away what the caller needs to act on — `NoWritablePeer` most of
all, whose remedy is a statement nobody ran rather than anything on the network.

**A redirect is not an error and does not arrive as one.** It is an instruction,
and a client that handles failures correctly — logs them, retries a bounded
number of times, gives up — handles an instruction encoded as a failure
incorrectly every time. So `reply.redirect` carries it, and `reply.outcomes` is
empty when it does.

## Writing a statement

The builder covers `SELECT`, `CREATE`, `UPDATE` and `DELETE` over one collection.
Anything else you write as a script and send as one, which is always available.

```python
query = (
    tessaridb.select("memories")
    .field("body")
    .where(tessaridb.compare("session", "=", tessaridb.Text("abc")))
    .order_by("created", descending=True)
    .limit(50)
    .render()
)
# SELECT body FROM memories WHERE session = $p0 ORDER BY created DESC LIMIT 50;
reply = conn.execute(query.script, query.parameters)
```

Filters combine with `&` and `|`, and both are **fully parenthesised** in the
rendered text. The parentheses are not an aid to reading: a builder does not
depend on the node's parser and therefore does not get to assume how `AND` and
`OR` associate, so writing them all makes the tree you built the tree that runs.

**A name is not a value.** A table or field name is grammar, so a parameter
cannot supply one and it is written into the text directly. That is safe only
because each is checked first, against a deliberately narrow production
(`[A-Za-z_][A-Za-z0-9_]*`), and a string that is not a name is **refused rather
than quoted into acceptance** — quoting would turn your mistake into a statement
that runs and means something else.

```python
tessaridb.select("memories; DROP COLLECTION memories; --")
# BuilderError: reason='not-a-name', what='a table'
```

The refusal is raised where the mistake was made rather than saved for `render`,
because the traceback then names the line. There are exactly two reasons —
`not-a-name` and `incomplete` — and the builder never invents a third; an
operator that is not one of the six is a plain `ValueError`, which is a different
kind of mistake.

**Object and `SET` fields render in ascending order of their names**, not in the
order you set them. Two builders given the same fields in different orders have
to produce the same text and the same parameter numbering, or the same query
written in two clients would be two statements.

## Watching changes

```python
watcher = tessaridb.connect("127.0.0.1:9080")
watcher.execute("USE NAMESPACE prod; USE DATABASE app;")

changes = watcher.subscribe(from_=0)
for change in changes:
    print(change.sequence, change.table, change.identity, change.removed)
```

**Subscribing consumes the connection.** After it, the socket delivers changes
and no longer answers statements; a client that wants both opens two. This one
refuses rather than hiding it, because hiding it would promise a multiplexing the
protocol does not perform.

**The node drops a subscriber that stops reading after 30 seconds**, and nothing
is lost when it does — the log is the buffer. So the loop above simply ends, and
the way back is a new connection subscribed from `changes.resume_from`, which is
the last sequence handled plus one. That arithmetic is this client's rather than
yours: resuming at a position already handled delivers it twice and resuming past
one reports being caught up, and both mistakes are silent.

## There is no TLS on this protocol

Credentials travel as given. Run this on a protected network, or behind something
that terminates TLS. It is a property of the protocol rather than an omission
here, and it is said out loud rather than left to be discovered.

## Values

The store's model has seventeen types, and two of its distinctions are easy to
lose in Python specifically.

**`none` and `null` are different** — the field is not present, versus the field
is present and holds nothing — and Python has one `None` for both, plus a habit
of using it for "missing" as well. Neither is spelled `None` here: they are
`NoneValue` and `NullValue`, with the singletons `NONE` and `NULL`.

**An integer is an `i64` and a decimal mantissa is an `i128`**, while Python's
`int` is arbitrary precision. Nothing overflows — which means nothing complains.
So every fixed-width write checks its range first, and a value that does not fit
is refused rather than truncated. This is the one job the other clients get from
their types for free, and skipping it would produce a value that this client's
own decoder reads back happily.

**A `Duration` is not a `timedelta` and a `Datetime` is not a `datetime`.** Those
types resolve to microseconds; the protocol carries nanoseconds, and converting
at the boundary would drop the last three digits without saying so.

**A `Decimal` is not `decimal.Decimal`.** The protocol carries an unscaled value
and a count of fractional digits — the two numbers that define an exact decimal —
and converting at the codec would decide rounding questions the protocol does not
ask. A caller who wants arithmetic converts.

Coordinates are **longitude first**, as RFC 7946 fixes, and travel as bits rather
than text. The opposite order is the most common bug in geospatial code precisely
because it is silent: a point in Paris becomes a point in the Indian Ocean, which
is a perfectly valid place.

## Conformance

The codec is checked against the corpus in the protocol repository, which is
generated by a **second implementation written from the specification alone**.
That matters more than it sounds: a codec that is wrong in the same way on both
sides round-trips perfectly, so a suite written alongside this codec cannot catch
what the corpus catches. Both directions are run — encode to exactly the stated
bytes, and decode to exactly the stated value.

The comparison used by those tests compares floats **by their bits**, because
Python's `==` says `nan != nan` and says `-0.0 == 0.0`, and the protocol
disagrees with it on both.

```
python3 -m unittest discover -s tests -t tests -v
```

The corpus is expected at `../tessaridb-protocol/conformance`, or wherever
`TESSARI_PROTOCOL_CONFORMANCE` points. A missing corpus fails loudly rather than
skipping: a suite that passes having found nothing reports coverage it does not
have.

The corpus establishes only that two implementations of a written document agree.
It does not reach a node, so the suite additionally **runs against one**:

```
TESSARIDB_TEST_NODE=127.0.0.1:47915 \
TESSARIDB_TEST_CLOSED=127.0.0.1:47917 \
TESSARIDB_TEST_USER=corpus TESSARIDB_TEST_PASSWORD=... \
python3 -m unittest discover -s tests -t tests
```

Those tests are opt-in and skip loudly when the variables are unset; a suite that
needs a server cannot be the suite that runs on a clean checkout. The second node
is a store with a user declared, because a session is the only thing that makes
credentials mean anything.

The query corpus is the same idea applied to text: the rendering must be
byte-identical and the parameter numbering must match, so that the same query
built in any client language is the same statement. Cases the contract says a
builder must refuse are asserted as refusals, with the stated reason, and are
never rendered. Neither the corpus nor the contract reaches the node's **parser**
— no client may link it — so every rendered case is additionally executed by a
node with its parameters bound, which is the only check that does.

Where a node cannot be asked — a peer that is not a node, a frame above the
16 MiB ceiling, an outcome tag from a future build, the three fields at the end
of a records answer that an older node simply omits — the tests are written
against the specification's bytes and say so. Each of those rules fails silently
when it is got wrong, and each was checked by breaking it deliberately and
watching the suite fail.

## Licence

Apache-2.0. The engine is licensed separately; the two are distinct decisions.
