# ADR: Mailbox poll health is recorded at the connect, and typed by cause
**Status:** Accepted
**Created:** 2026-07-31
**Last updated:** 2026-07-31
**Decided by:** claude-opus-5 (LUM-633; self-review + fable-5 R1 + sonnet-5 R2)

## Context

LUM-690 gave mailboxes a health field (`sources.last_poll_error_code`, migration `063`) so a mailbox that could not authenticate stopped failing silently. It stamped that field at one place: immediately after `EmailImapSource(source)` returned.

`EmailImapSource.__init__` does not open an IMAP socket. It stores the source and loads the credential row. The socket opens later, in `_connect`, inside `imap_egress_scope`. So a mailbox blocked by the LUM-625 egress layer constructed successfully, was stamped **healthy**, and then failed at a connect that stamped nothing.

The surface therefore did not merely omit the failure — it **affirmatively reported the mailbox as fine**, and its single message ("Lumogis can't currently sign in to this mailbox") sent that user to re-enter a password that was perfectly good. That is worse than the operator-log-only state LUM-633 was originally filed against.

Two further facts shaped the decision. `poll()` connects *and* fetches inside one call, so the caller cannot tell the phases apart without help. And DNS failure was raised as a bare `ValueError` from a helper called at **both** the construction and connect sites, so any per-site classification would give the same condition two different meanings.

## Decision

**Health means *connected*, not *constructed*.** It is recorded at every connecting call — the metadata seed and poll, the full-ingest poll and body fetch, the backfill slice and its UIDVALIDITY reseed — through one shared helper so the sites cannot drift.

**The phase boundary is made real, not assumed.** `_connect` wraps its failures in `ImapConnectError(kind)`, re-raising `EgressBlockedError`, `ImapHostBlockedError` and `ImapHostUnresolvedError` unwrapped because other layers catch those by type. `kind` is decided at the wrap site, since `imap_transport` is by convention the sole importer of `imapclient`.

**Anything unattributable to the connect clears health rather than stamping it.** Connect precedes fetch, so an exception that is not connect-typed proves the mailbox connected.

**Four codes, because the right user action differs:** `credential` (reconnect), `egress_blocked` (operator policy; credentials are fine), `network` (usually transient), `config` (never self-heals). `ImapHostUnresolvedError` was added so a transient DNS failure is `network` at *both* sites rather than `config` at one.

## Alternatives Considered

- **A single classifier for both sites** — cannot work. `poll()` raises bare `ValueError` post-connect (`int()` over server-supplied UIDVALIDITY/UIDs), so any catch-all default stamps fetch garbage as a connection fault.
- **A `self._connected` flag on the adapter** — ambiguous, because the full-ingest path opens a second connect in the same tick; a flag cannot say which one the outcome refers to.
- **Three codes, folding config into network** — tells a user with a bad port that the problem "usually clears up on its own", which it never will.
- **Stamping mid-fetch failures too** — makes a mailbox flap red/green on one malformed message, which is how an indicator earns being ignored. Migration `063`'s header already drew this line and it is upheld.
- **String-matching exception messages to detect auth** — fragile and provider-dependent; rejected in favour of typing at the wrap site.

## Consequences

**Easier.** A green mailbox now means a mailbox that connected. The code is a shared vocabulary an operator can grep — `egress_blocked` is spelled identically here and in the write-back layer's `PermanentActionError`. Other connectors (caldav, paperless) can adopt the same shape.

**Harder.** The `except` order inside `_connect` is load-bearing and not visually obvious: `ImapHostBlockedError` and `ImapHostUnresolvedError` are `ValueError` **subclasses**, so a broad `except ValueError` placed above them makes `_connect_writeback`'s dead-letter clause dead code and retries a permanent policy block forever. `tests/test_imap_connect_errors.py` drives the real `_connect` for exactly this reason — every other test injects a pre-built exception into a fake and cannot see the mistake.

**Future work must know.** Adding a row to one classifier requires checking the other: they run at different sites over overlapping types, and if they disagree one condition gets two stories. Adding a code requires UI copy; the client falls back safely for codes it does not know, so server and client may ship out of step.

**Not proven.** These checks say nothing about a mailbox that connects and then returns wrong data. Health is a connection property only.

## Revisit conditions

- **If a third caller starts catching `ImapConnectError` by type**, the exemption list needs re-auditing — it is currently sized for `_connect_writeback` alone.
- **If mid-fetch failures turn out to matter more than flapping** — e.g. a mailbox that connects but never returns messages — the connect-only boundary should be re-argued with evidence, not quietly widened.
- **If a second connector adopts this pattern**, factor the classifier and the shared stamping helper rather than copying them; the third copy is where they drift.
- **If `OAuthRefreshError` gains a network-vs-credential distinction**, revisit its classification — today a transient token-endpoint blip reports as `credential` and tells the user to reconnect (tracked separately).

## Amendment — LUM-760: `config` must name a fix-owner, and cannot name which one

This ADR owns the four health codes and their rendered copy. `config` was worded for settings a household member **typed** — *"this mailbox's settings can't be used as entered … the settings need correcting"*.

Since **LUM-757** that is only half true. `config` now also covers OAuth conditions the user could not have caused: a missing operator `LUMOGIS_OAUTH_*_CLIENT_ID`, an unknown provider, a provider answering `invalid_client`. The old copy told them they had mistyped something they never typed — the exact substitution this ADR's taxonomy exists to remove, surviving in the copy rather than the classifier.

**The obvious fix is unavailable, and that is worth recording.** LUM-760 proposed splitting the copy by auth mode — "the health row knows whether the source is OAuth or password-authenticated". It does not: `pollHealthMessage` receives **only the code**, `SourceRow` carries no auth field, and `GET /sources` serialises none. LUM-769 hit the same seam from the other side and deferred publishing the auth method to LUM-650.

**So `config` names both possible fix-owners and asserts neither** — "if you entered them yourself, a setting like the port or encryption needs correcting; if you connected by signing in with the provider, ask your Lumogis operator". This matches `egress_blocked`, which already named its owner and was the comparison the ticket used.

**The rendering contract is unchanged** — still one code, one string, no new state. What changed is a standing requirement on the copy: **a code that spans two fix-owners must name both.** If the auth method is ever published, `config` should split rather than keep hedging, and that is the revisit condition.

**Met by LUM-777 ([ADR-225](225-lum-777-mailbox-auth-method.md)).** `GET /sources` now publishes `auth_method`, and `config` and `credential` split by method when it is known. When it is unknown, the copy still names both owners, so the standing rule holds.

## Status history
- 2026-07-31: Accepted — implemented and verified on `dev` (`5090b365f`). Plan carried 5 P0s across two review rounds, 4 of them introduced by fixes rather than present in the original draft.
