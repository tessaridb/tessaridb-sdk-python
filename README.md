# tessaridb-client

A client for [TessariDB](https://tessaridb.com) in Python, written from the
[protocol specification](https://github.com/tessaridb/tessaridb-protocol) and
nothing else.

> **Status: early.** The value codec and the wire connection are in — the
> greeting, statements with bound parameters, answers, refusals and change
> subscriptions — the query builder, whose every rendering is executed by a
> running node, and the HTTP surface for objects, files, backup and health. Each
> is proven against the shared conformance corpora and exercised against a
> running node.

```
pip install tessaridb-client
```

Python 3.11 or newer. Apache-2.0. **No dependencies** — the standard library
covers the protocol, and this package handles credentials, so every dependency
would be supply-chain surface inside it.

The distribution is `tessaridb-client`; the import is `tessaridb`.

## Versions, and what actually has to match

This client's version is **its own** and never tracks the engine's. A fix here
would otherwise force an invented engine release, and an engine release would
force five invented client releases.

What has to match is the **protocol**. This release speaks **protocol 1.1** and
connects to any node of protocol **major 1**, which is checked in the greeting
before anything else is sent — a differing major is refused there rather than
discovered mid-conversation, where it arrives as a decode failure that reads
like corruption. A differing *minor* is not a refusal: the peer's minor is
reported so a caller can decline to send what an older node cannot read.


## What works today

|                                                    |                                |
| -------------------------------------------------- | ------------------------------ |
| value codec — all seventeen types, both directions | **done**, 54/54 corpus vectors             |
| wire connection, greeting, statements, answers     | **done**, exercised against a running node |
| change subscription                                | **done**, exercised against a running node |
| query builder                                      | **done**, 38/38 corpus, 26 executed by a node |
| HTTP surface — objects, files, backup, health      | **done**, exercised against a running node    |
| JSON values and outcomes — §5.6, §5.7              | **done**, 59/59 values, 20/20 outcomes        |
| session token — §5.8                               | **done**, open once, `Bearer` thereafter      |
| `/watch`, `/metrics`, `POST /password`             | not yet                                       |

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

On a cluster, two more clauses say **which node may answer** rather than what the
answer holds:

```python
query = (
    tessaridb.select("orders")
    .staleness("30s")        # no node further behind than this may answer
    .answered_by("LEADER")   # and it must be the node that decides writes
    .render()
)
# SELECT * FROM orders STALENESS 30s ANSWERED BY LEADER;
```

They are separate controls rather than one: a follower at zero lag is *level*,
not authoritative. A bound tighter than the cluster can know about itself is
refused **by the node**, and the refusal names the floor — this client checks the
shape of a span and never its value, because the floor belongs to the cluster.

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

**A subscription the node declines is refused on the feed**, not at the
`subscribe` call — the node reads the frame before it can judge it. The commonest
cause is the one the example above avoids: a connection that has named no
namespace has no `thing` to watch. It arrives as `Refused` carrying the node's
own words, because reading it as an unknown frame sends whoever meets it to the
protocol when the answer is a statement they did not run.

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

## Objects, files and health

Everything the wire protocol does not serve is here, and it is a different client
because it is a different surface rather than an alternative to the first one.

```python
node = tessaridb.HTTPClient("127.0.0.1:8000", user="app", password=os.environ["TESSARIDB_PASSWORD"])

node.put("acme", "app", "uploads", "reports/100% done.pdf", content)
back = node.get("acme", "app", "uploads", "reports/100% done.pdf")
listing = node.listing("acme", "app", "uploads")
condition = node.health()
```

**The password is spent once.** A node verifies Basic with Argon2id at the OWASP
floor, and HTTP has no connection to hang a session on, so that cost is paid on
_every_ request that carries one. This client opens a session on its first
authenticated call and presents the token after — and when a token stops working,
which it does four different ways that all answer `401`, it signs in again and
retries once, without the caller seeing it. A client that skipped this would be
correct, would pass every test, and would be slower than the protocol intends by
more than an order of magnitude.

**`node.script()` takes no parameters, and that is deliberate.** A parameter on
this route is a JSON string carrying _TessariQL source_, not a value —
`{"x":"3"}` is the number 3 and `{"x":"hello"}` is a `400`. Passing a caller's
string through would be a type-confusion hazard that no test written against it
would show, so this client does not build the bridge: a statement with a value in
it goes over the wire, where a parameter is an encoded value and none of this
arises.

**A `404` is an answer.** A file that is not there reads as `None` and a file that
exists and is empty reads as zero bytes — these are different facts and the
server draws the line, so this client does not erase it. A listing that comes
back `None` means the name is not a bucket; an empty tuple means the bucket is
there and holds nothing.

`HEAD` is deliberately not offered rather than pending. The node reads the whole
object and discards the body, so it costs the server exactly what a `GET` costs;
presenting it as a cheap `exists()` would be an invitation to call it in a loop.

## A value read over HTTP needs its kind

JSON has six types and the store has seventeen, so §5.7 is a decision rather than
a translation: for most of the table the type is **not recoverable from the JSON
alone**. `"12.34"` is a decimal or a string, `"1h30m"` is a duration or a string,
and `users:7` is the integer 7 or the text `'7'`.

So the reader is told, and a caller reads the kind from the field's declaration
in the catalog:

```python
outcomes = node.script("RETURN 1;", tessaridb.Reading(tessaridb.IntegerKind()))
outcomes[-1].value      # Integer(value=1)
```

A reader that guessed instead would be right most of the time, which is worse
than being wrong all of it. If you need types without carrying a catalog, use the
wire protocol, where every value carries its tag.

One spelling stays lossy even with the kind supplied, and this client says so
rather than papering over it: a float `-0.0` is written `0`, because the value is
normalised before it is written. No reader can tell it from `+0.0`.

**A table and a record identity are strings here and are not parsed back.**
`users:7` is the integer 7 and the text `'7'` written identically, and the
conformance corpus carries one `keys` outcome holding `"1"` beside `"ada"` — an
integer identity and a text one in a single array, which no declared kind could
cover. They are identifiers to display, log and pass back.

## What this node does that the specification does not

Found while writing this client against `protocol-v1.md` alone, measured against
a `0.0.5-alpha` node, and asserted in the live tests so they are loud rather than
silent — each of those tests fails the day the node is fixed, which is the right
direction for it to fail in. **Two of the three have since been fixed and their
tests are flipped**, measured against `0.3.0-beta`.

**Fixed.** `GET /files/{ns}/{db}/{bucket}` now answers `{"files": […]}`, the
§5.1 shape, and every name that is not a bucket answers `404`. `0.0.5-alpha`
answered a whole records outcome wrapped in `files` and `200` for a collection's
name. This client still refuses the wrapped shape rather than reading it,
because such an element carries a `path` key holding the access path, so a reader
that trusts it returns a file called `scan` and reports success — an old node is
still an old node.

**Not a divergence, and a trap all the same.** A listing's `path` carries a
leading slash, exactly as §5.1's example shows, and it is **not** the name that
was written: a file `PUT` as `notes.txt` lists as `/notes.txt`, and one named
`/notes.txt` lists as `//notes.txt`. Feed a listed path straight back to `get`
and the request carries a double slash and answers `404`. Strip **one** leading
slash, never `lstrip("/")` — a file may genuinely be named with one, and
stripping them all reads a different file and reports success.

A repeated Basic sign-in earns `429`, a status §5.2 does not enumerate, and the
node then refuses that user's **correct** password. That interacts directly with
§5.8's prescribed recovery, so this client retries a `401` once and never a
`429`, and it remembers a store that has no session to open rather than asking
per request.

`GET /backup` is chunked **once the log is big enough**, which §5.3 forbids on
every route and names this one specifically. The condition is the point:
measured against `0.3.0-beta`, a ~23 kB backup declared `Content-Length` and a
~38 kB one chunked, so a test on a fresh store reports the divergence fixed and
production meets it anyway. The live test now writes past the threshold first.
This client reads either framing: §5.3's refusal is for a framing a client does
not recognise, and chunked is recognised.

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

The JSON corpus is decode-only, and it says why: a client never encodes a value
on that surface, since a `/script` parameter carries TessariQL source rather than
JSON. So it is weaker than the value corpus by construction — there is no writer
for a wrong reader to agree with — and what it does catch is every place the JSON
is lossy and the declared kind is what restores the value.

Where a node cannot be asked — a peer that is not a node, a frame above the
16 MiB ceiling, an outcome tag from a future build, the three fields at the end
of a records answer that an older node simply omits — the tests are written
against the specification's bytes and say so. Each of those rules fails silently
when it is got wrong, and each was checked by breaking it deliberately and
watching the suite fail.

## Licence

Apache-2.0. The engine is licensed separately; the two are distinct decisions.
