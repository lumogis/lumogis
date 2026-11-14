# ADR-217 — Per-user source creation

**Status:** Accepted
**Date:** 2026-08-23
**Ticket:** LUM-839
**Relates to:** ADR-192 (paperless mailbox overlap), ADR-215 (LUM-837 source edit and health), ADR-216 (LUM-838 outbound policy), LUM-626 (multi-instance credentials — *related, not blocking*)
**Supersedes:** the admin-scoped surface drafted in `cursor/adrs/LUM-837-paperless-connect-surface.md`, per its own decision **D2**

## Context

`POST /sources` was the only source verb still `require_admin`:

| Route | Authz before |
| --- | --- |
| `GET /sources` | per-user, owner-scoped |
| `PATCH /sources/{id}` | per-user, owner-scoped |
| `DELETE /sources/{id}` | per-user, owner-scoped (404, no existence oracle) |
| `POST /sources/{id}/sync` | per-user |
| **`POST /sources`** | **`require_admin`** |

Read, edit, delete and sync were already per-user. **Create was a lone anomaly**, and the handler was *already* fully per-user internally — both consent arms, the `INSERT`, and `schedule_source` all read `user.user_id`. Nothing in `docs/decisions/` argued for the gate.

**One honest correction to the "it was never designed" reading:** `tests/test_auth_phase2.py` did list `("POST","/sources")` in `_EXPECTED_ADMIN_PATHS`, enforced against the live route table. That is a *regression guard* — it records that the dependency exists, not why — but the original ticket and plan both claimed no test asserted it, and that was wrong.

## Decision

**Source creation is per-user, uncurated, with two resource guards.**

### 1. `require_admin` → `require_user`

Owner-scoped like its siblings. Attribution is unforgeable: `user_id` comes from `UserContext` and no request-body field feeds it.

### 2. No admin curation — at either level, and the distinction matters

Two different questions were asked and both answered *no*:

* **Instance-level** ("the admin approves this particular feed") — rejected. An approval queue is the problem this ticket exists to fix, wearing a UI. In a household the admin is a family member, not an IT department.
* **Type-level** ("members may add RSS but not paperless") — rejected, and note there is **no precedent for it anywhere in the product**. Mailboxes (`POST /me/email-sources`), LLM keys (`put_my_credential`) and MCP tokens (`mint_my_token`) are all already per-user self-service. `POST /sources` was the sole inconsistency, not the start of a liberalisation.

**The safety argument is a property of the system, not a preference.** A source's signals are written `scope='personal'` (`services/signal_processor.py:800`; only the `__system__` sentinel writes `system`), and `visibility.py`'s rule is `(scope='personal' AND user_id=$me) OR scope IN ('shared','system')`. **A member's feed is invisible to the household**, so a poor choice degrades only its chooser. Curating something that affects only the curator is pure friction.

### 3. The forward rule: gate on blast radius, not on type or role

> **Read-only and personal-scope → any member. Executable, or household-visible → admin.**

RSS, page and paperless sources are the first. A future **external MCP server attachment** — Lumogis calling *out* to a third-party tool server — is the second, and should be admin-gated. `/me/mcp-tokens` is **not** that: it is the inbound direction (a member minting a token so their own client reaches their own data), which is why it is already per-user.

A household/shared source tier does not exist. If one lands it is a `shared`-scope source that keeps `require_admin` — **the gate rides on the source's scope, never on the creator's role.** Do not reintroduce a role check on this route to get it.

### 4. Two guards, answering different questions

| | Bounds | Why it exists |
| --- | --- | --- |
| **Detect limiter** — 10/60s per user | *burst* | Detection performs several outbound fetches per call, and preview is deliberately exempt from the cap, so this path would otherwise have no bound at all |
| **Source cap** — 100/user, `LUMOGIS_MAX_SOURCES_PER_USER` | *accumulation* | Feeds poll forever once created |

**Neither substitutes for the other.** The limiter mirrors `_email_test_limiter` exactly — same route shape (`require_user` + outbound work), same per-user key, same window — rather than inventing a fourth pattern.

**The limiter must refuse before `_detect_source` runs.** A limiter placed after the work still returns `429` and still passes a naive test while having already made the requests it exists to prevent. Pinned by `test_the_limiter_refuses_before_any_outbound_fetch` and mutation-checked.

**The cap sits above the type branch, guarded on `body.confirm`.** `add_or_preview_source` contains **two** `INSERT`s (paperless and detect); a check beside either leaves the other uncapped. `body.confirm` — not the check's position — is what exempts preview.

### 5. The cap counts only what this route creates

`source_type IN ('paperless','rss','page')` — **IMAP mailboxes are excluded.**

Considered and rejected: counting everything, on the argument that the cap guards shared signal-processing compute and a mailbox consumes it. **The deciding argument was actionability, not compute.** Mailboxes come from `POST /me/email-sources`, which is uncapped and keeps adding to the counted set, and they are not displayed on `/me/sources`. A member refused over rows they can neither see nor delete from the refusing surface cannot act on the refusal — the message could explain the wall but never remove it. Every row that counts is now one the user can act on.

The Python constant and the web's `MANAGED_TYPES` are **one idea in two languages**, and drift would reintroduce exactly this bug — so a test reads the TSX and pins them equal rather than a comment asking nicely.

### 6. The cap is best-effort, not atomic — deliberately

`COUNT(*)` then `INSERT` with no transaction means two concurrent confirmed creates at the ceiling can both pass and land one row over. Accepted: this is a **resource guard, not a safety control** (personal scope is the safety property), and it already fails open on query error. A serializable transaction would buy a precision the design does not want and put a lock on a human-initiated path.

## Consequences

**ADR-192's "paperless is admin-owned and single-instance" premise stops being true** for the admin-owned half. Its **same-user-by-construction scoping survives intact**, because every scoping call already passes `user_id` (`rule_bearing_mailboxes(user_id)`, `overlap_for_sources(user_id, …)`). Both consent arms were verified already `user_id`-scoped; their meaning shifts from *the admin's* mailboxes to *the caller's own*, which is what they should always have meant.

**ADR-192 D4's household gap is now closable rather than structural** — a member can own a paperless source, so there is something to scope a cross-member check to. It is **not closed here**: closing it means cross-user overlap checking, which is a privacy decision (it tells A something about B's mailboxes).

**The `/me` shell now carries two source-shaped surfaces**, and that is intended: `/me/sources` (rss / page / paperless) and `/me/email-signals` (mailboxes, created through a different route with its own guided setup). They are not merged.

**The screen had to move.** `App.tsx` gates the Admin nav on `role === "admin"`, so flipping the backend alone would have authorised members to use a page they could not reach.

**LUM-626 is not a dependency.** The ticket asserted per-user sources would point two members at one shared credential slot; credentials are keyed `(user_id, connector, instance)`, the poller loads via `source.user_id`, and two members already resolve to two rows. LUM-626 covers *one user with two archives* — orthogonal. Disproved independently twice (plan authoring and critique round 1) before the ticket text was corrected.

**Not addressed here:** a household-wide admin view over everyone's sources (needs a privacy decision first), and re-assigning admin-created sources to the member who uses them — on existing installs every source belongs to the creating admin.

## Revisit conditions

- **The default scope of source signals changes.** `scope='personal'` is the *entire* safety argument for uncurated per-user creation. If source signals ever become household-visible by default, this decision must reopen — curation becomes arguable the moment one member's choice reaches another member's surface.
- **An attachable thing arrives that is executable or household-visible** — an external MCP server being the obvious one. Gate it on blast radius per §3, and do not reach for a role check on this route.
- **The cap or limiter starts refusing legitimate use.** Both numbers are judgements, not measurements: nothing measures the per-source cost of signal processing, and 10/60 was copied from a route that tests one mailbox rather than one that might see several feeds pasted in a sitting.
- **A household source tier is wanted.** Build it as `shared`-scope with its own admin gate; do not re-admin this route.

## Status history

- 2026-08-23: **Accepted.** Implemented with 20 backend tests and 2 web tests, mutation-checked eight ways (each killing only its own tests, including the limiter-position check where a limiter moved after the fetch still returns 429 and still passes a naive test). Two critique rounds (fable-5, sonnet-5) found four defects that would otherwise have shipped: a coverage gate asserting the admin dependency, the second `INSERT` leaving paperless uncapped, a missing sibling component breaking the web build, and a preview error handler telling rate-limited users to "try again". Cap scope (exclude mailboxes) settled by the owner during arbitration.
