# ADR-216 — Outbound policy for the RSS / web-page source class

**Status:** Accepted
**Date:** 2026-08-20
**Ticket:** LUM-838
**Relates to:** ADR-174 (IMAP host validation), ADR-178 (Squid CONNECT tunnel), LUM-281 (connector base-URL validation)

## Context

Every source class in this product had an explicit, ADR-backed outbound guard
except one:

| Source class | Guard |
| --- | --- |
| Connectors (paperless, caldav…) | `validate_outbound_connector_base_url` — scheme allowlist, rejects credentials in the authority, resolves and checks private / link-local / metadata IPs against an allowlist |
| IMAP | `_validate_imap_host` (ADR-174) plus a Squid `CONNECT` tunnel allowlisting mail hosts by SNI (ADR-178) |
| **RSS / web page** | **none, anywhere** |

Not at detection, not at poll. The gap was never argued for — the other two
paths each got a dedicated decision and this class was simply never looked at.
Nothing in `docs/decisions/`, `cursor/plans/` or `cursor/explorations/` mentioned
`detect_signal_source` or `_detect_feed_url` before LUM-838.

**Severity, stated fairly.** `POST /sources` is `require_admin` and is the only
creator of `rss`/`page` sources, so this was an authenticated household admin
reaching their own LAN — not anonymous SSRF. And reaching private hosts is *by
design*: `LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS` defaults to `true` because
`paperless.lan` is the ordinary case in a self-hosted product. The real delta was
non-`http(s)` schemes, credentials embedded in the authority, link-local and
metadata addresses when an operator has turned the flag off — and
`follow_redirects=True` throughout, which bypasses entry-point validation even
where validation exists.

## Decision

**Reuse the connector validator; add only what it does not cover.**

`services/outbound_signal_fetch.py` wraps `validate_outbound_connector_base_url`
rather than writing a fourth guard. That validator already encodes the scheme
allowlist, the credentials rejection, and the private-IP policy with its env flag
and allowlist. What this class needed on top was **safe redirect handling**.

### Redirects are followed manually and every hop is validated

Validating an entry URL while leaving `follow_redirects=True` is a partial fix
that reads like a complete one: the first hop is checked and every subsequent one
is not, so `https://harmless.example/r` → `http://169.254.169.254/` walks past the
guard.

Rejected: **disabling redirects.** `http`→`https` upgrades, feed hosts and moved
domains all redirect legitimately; a feed reader that refused them would break
real sources and buy nothing.

Chosen: follow manually, up to 5 hops, validating each `Location` before fetching
it. Relative `Location` headers are resolved against the hop they came from, so
the URL validated is always the URL fetched.

### One choke point per adapter, and no raw `httpx` left

Both adapters route every outbound call — detection, poll, and `ping` — through a
single helper. `grep -c httpx` returns **0** for both `rss_source.py` and
`page_scraper.py`. A guard that can be bypassed by adding one more `httpx.get`
is a guard with a short life; this is the structural half of the fix.

### The private-address default stays shared

`LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS` keeps its `true` default for this class
rather than being tightened for it:

- Watching an internal wiki page or a LAN service's feed is ordinary in a
  self-hosted product — the same reasoning that makes `paperless.lan` normal for
  connectors.
- A per-class divergence would be a fourth policy for a reader to hold in their
  head, which is what this module exists to avoid.
- The flag is what an operator sets when they want it off. **Before this change
  it did not apply to this class at all; now it does** — that is the actual
  improvement, and it is available to anyone who wants the stricter posture.

So "can reach 192.168.x" is deliberately **not** what this closes.

### A refusal is distinguishable from a failure

`OutboundURLBlocked` is raised rather than returning `None`. A blocked URL is a
decision the product made and can explain; an unreachable one is a fact about the
world. Collapsing them is how a refused source ends up reported as *"no feed
found"* — which tells the user nothing they can act on, so they retype the same
address and get the same nothing.

`POST /sources` validates the submitted URL up front and returns
`422 {"code": "source_url_not_allowed", "message": …}`, so the refusal is legible
at the point of entry. The per-hop checks inside the fetch helper are
defence-in-depth and cover poll time, where there is no user to tell.

## The `file:///` question, closed by construction rather than by testing

LUM-838 flagged one item it could not verify: `feedparser.parse()` accepts a URL,
a **local filename**, or raw data — so if it resolved `file:///…`, detection was
an arbitrary local file read through the API, a different class of problem from
LAN reachability. It was untestable where the finding was made, because
`feedparser` is a Docker-only dependency.

**It is now moot.** LUM-843 changed every `feedparser.parse` call to take a
**body string** rather than a URL, so feedparser performs no I/O of its own at
all. Verified: `grep -rn 'feedparser.parse(' orchestrator/` shows every call site
passing a fetched body. This is closed by construction, not by a container test —
which is a stronger guarantee, since it cannot regress without someone
reintroducing a URL argument, and it removes the need to re-verify against each
new feedparser release.

## Consequences

**What is now refused** that was not: non-`http(s)` schemes (including
`file://`), credentials in the authority, whitespace-padded URLs, link-local and
metadata addresses when the flag is off, and any redirect hop violating the same
rules.

**What is still allowed**, deliberately: LAN and private addresses under the
default flag, and ordinary redirects.

**`ping()` on both adapters now returns `False` for a policy-refused URL.** That
is a behaviour change, and the right one — a URL the product will not fetch is
not reachable in any sense a caller of `ping` cares about.

**Poll-time coverage came free** from the choke-point design: the same helper
serves detection and polling, so there is no version of this fix that hardens the
door and leaves the window open.

**Not addressed here:** the orchestrator still has no network-layer egress
backstop for general outbound `httpx` — the Squid work (ADR-178) is an IMAP
`CONNECT` tunnel and does not cover this. Application-layer validation is what
this ADR provides; a network-layer control remains a separate question.

## Status history

- 2026-08-20: Accepted. Implemented with 16 tests, mostly negative, including
  four redirect cases. Mutation-checked: reverting to entry-only validation fails
  exactly the two blocked-redirect tests.
