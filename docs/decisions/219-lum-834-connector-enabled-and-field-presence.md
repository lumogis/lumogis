# ADR-219 — A connector you can switch off, and a card that says which fields are set

**Status:** Accepted
**Date:** 2026-08-24
**Ticket:** LUM-834 (chunk B — G3 enable/disable, G4 field presence, the unified card)
**Relates to:** ADR-218 (chunk A — the probe registry), ADR-199 (avoid decrypt-on-enumerate), ADR-214 (sending address), ADR-197, LUM-846 (forms destroyed the stored secret), LUM-848 (imap/smtp cards could not save)
**Supersedes nothing.** Extends ADR-218 rather than replacing it.

## Context

Chunk A shipped a test-connection probe and left three P1 gaps. "Disable" was
`client.delete` — it **destroyed the credential**, so turning a connector off
and on again meant re-entering the secret. Forms rendered blank and the list
showed `connector` + `updated_at` only, so nothing said *which fields are set*.
And Ask/Do lived on a separate page.

The hard constraint throughout is ADR-199's: **no decrypt on the management
surface**. Every design below is shaped by it.

## Decision

### 1. `enabled` is a new column, not an overload of `delivery_paused`

`delivery_paused` (migration 021) is **system-set** on delivery failure and
cleared on recovery, read at two notification-only sites. `enabled` is
**user-set** and must gate ingest and actions too. One column with a reason
discriminator would let a system auto-pause overwrite a user's deliberate off,
and let recovery silently switch a connector back on. Effective state is
`enabled AND NOT delivery_paused`.

`DEFAULT TRUE` is the only safe default: every existing credential is in use at
the moment the migration runs.

### 2. The gate is a **list of touch points**, and the number is not written down

Three consecutive review rounds corrected this count and **all three
corrections were themselves wrong** — one, then two, then four, and R3 found a
fifth. The count is what kept being wrong, so the ADR and the plan both carry
the rows instead:

| Touch point | What changes |
| --- | --- |
| `get_payload` | `AND enabled` in the SQL |
| `ccs.resolve` (SELECT) | duplicates that SQL; same predicate |
| `ccs.resolve` (env fallback) | `is_disabled` before `_resolve_env_fallback` |
| The loader env-fallback branches | `is_disabled` before falling back |
| `resolve_runtime_credential` tier 1 | a second read distinguishing disabled from absent |

**Why a second read is unavoidable.** With `AND enabled`, *disabled* and
*absent* both yield `None` — one bit for two outcomes that must differ. Absent
falls through to the household tier; disabled must stop. `ntfy_runtime`'s
shipped `delivery_paused` handling works precisely because it re-reads the
record after resolving, and copying its conclusion without its mechanism is
what the earlier design did.

**Why the loader guards matter more than they look.** `AUTH_ENABLED` defaults
to **false**. In that deployment every loader falls back to env vars when
`get_payload` returns `None` — so a gate that stopped at the SQL would have
made disabling a **complete no-op** for the ordinary single-user install:
ingest keeps running against the env-configured server and the toggle appears
broken.

### 3. No new exception class — `ConnectorNotConfigured` is reused

A `ConnectorDisabled(LookupError)` would be a **sibling** of
`ConnectorNotConfigured`, so `except ConnectorNotConfigured` could not catch
it, and it would have escaped from the lowest credential primitive into route
bodies doing bare truthiness checks — a user switching a connector off would
have produced a 500 on an admin screen. Reusing the exception the codebase
already routes correctly means chat keeps its 424 and ingest keeps skipping,
with no consumer changes.

The cost is honest: **the two states are indistinguishable by type**, only by
message, and on the caldav and ntfy paths not even by message — both loaders
catch the resolver's raise and substitute their own text.

### 4. Field presence comes from a stored key-name column, never from a decrypt

`configured_keys TEXT[]` holds the payload's **key names**, written by
`put_payload` from `sorted(payload.keys())` in the same `INSERT` as the
ciphertext. Deriving presence from the payload instead would add a decrypt to
*every connector on every render of the connectors screen* — the surface
ADR-199 removed — and it cannot describe an **unconfigured** connector at all,
which is the state the card most needs to render.

**NULL is load-bearing and means UNKNOWN.** A row written before this migration
cannot be populated truthfully without decrypting every credential. `NOT NULL
DEFAULT '{}'` would therefore have made every pre-migration credential report
"no fields configured" — rendering a working connector as unconfigured, which
is ADR-199's named failure mode arriving through a migration default. The
triad follows `014-mcp-tokens.sql` D3 exactly, pinned by an in-DB `COMMENT`.

This is a **second copy of the payload's shape**, the class ADR-197 and ADR-214
closed. Three things make it defensible: it cannot drift silently (same
statement, same payload); it is **not authoritative** for anything (nothing
loads a credential through it, so a wrong value is a wrong tick, not a failed
connection); and the alternative decrypts on every render.

### 5. `secret_fields` comes from the registry, `configured_fields` from the row

Secrecy is a property of the **connector**, not of one user's payload. The
split matters at the projection: forgetting the registry injection does **not**
fail validation, because the wire default is `[]` — every response would simply
claim the connector has no secret fields and the card would render values that
should be masked as ordinary rows. The loud failure (the record's
`configured_keys` reaching an `extra="forbid"` model) is the safe one; this is
the quiet one, and it has its own test.

### 6. Set/not-set is **display only** — "leave blank to keep" is not built

LUM-846 made every form require its secret on **every** save, because
`put_payload` is whole-payload replacement and omitting a field deletes it.
Making "leave blank to keep" honest needs a backend **merge** — a
read-modify-write over plaintext on the management surface, the posture
ADR-199/214 guard. That deserves its own ADR, not a UI chunk.

The resulting UX mismatch is handled **at the marker, not the posture**: a
secret field renders consequence copy ("saved. Changing anything on this card
means entering it again") rather than a bare `✓ set`, which would announce that
the value is stored and then refuse to use it.

### 7. `imap` and `smtp` render no card — but a signpost, not silence

Both are credentialed per mailbox and this surface PUTs without an `instance`,
so every save was `422 instance_required` (LUM-848). Hiding them alone would
recreate the same defect inverted: a user looking for "where do I configure my
email?" would find **nothing**. A one-line signpost to Email signals is the
difference between removing a dead end and removing the map.

### 8. G5 (inline Ask/Do) is **not shipped**, and the reason is a conflict between two decisions

`PUT /api/v1/me/permissions/{connector}` validates against the **action**
registry, whose only members are `imap` and `smtp` — because a permission mode
gates *actions*, and no other connector registers any. Those two are exactly
what decision 7 removes from this card. The control would therefore have
rendered on paperless, caldav, ntfy and the six `llm_*` and returned
`404 unknown_connector` on **every** change, while the list GET returned no row
for them so it always read "Ask me first".

**Neither decision is wrong on its own, which is why three critique rounds
passed it.** Every round read G5 and the imap/smtp hiding separately; nothing
asked whether the set of connectors G5 needs and the set decision 7 removes
were the same set. They were.

Shipping a control that 404s on every click is the defect this arc is named
after, so it was removed.

**And the setting does not belong on that page anyway** — which is the finding,
rather than the wiring. Ask/Do only means something for a connector that acts
**on your behalf**, and none of the nine cards there can: paperless and caldav
read, ntfy pushes to you, the `llm_*` ones answer questions. Nothing leaves as
you. Email is the only family that acts.

**The resolution (LUM-849): keep `/me/permissions`, fix its copy, cross-link to
it — do not move it.**

The tempting move is to fold Ask/Do into Email signals and delete the page. It
was considered and **rejected**, and the reason is recorded here because the
idea is attractive on its surface and will otherwise be re-proposed.

`/me/permissions` is not the email settings page; it is the **autonomy** page —
*what may Lumogis do on my behalf, and does it ask first.* Email is the only
occupant of its connector-mode half, but the second occupant is not
hypothetical — it is **already enforced in the executor**:

| Surface | Route | Answers |
| --- | --- | --- |
| Permissions | `/me/permissions` | connector Ask/Do modes (`imap`, `smtp`) |
| — none — | `GET /api/v1/me/permissions/capabilities` | capability scope grants (LUM-612). **Served, and rendered by nothing.** |

Folding Ask/Do into Email signals would encode *"autonomy = email"* into the
information architecture, and capability modes — which are enforced today —
would then have nowhere coherent to sit: either a third page (worse than today)
or a second move of the email setting, after people had learned it lived under
Email. Moving a **security** setting once people know where it is is the
expensive kind of change; doing it twice is worse.

**What the page actually needs is copy, not relocation.** It renders raw
connector ids and raw modes — `imap`, `smtp`, `ASK`, `DO` — with no sentence
saying what is being decided or what it covers. That is the defect. It becomes
*"What may Lumogis do without asking?"* with each row stating its scope
(**"applies to all your mailboxes"**), a name that says autonomy rather than
access control, and a signpost from Email signals — the same pattern decision 7
used for the hidden `imap`/`smtp` cards, for the same reason.

**Per-connector is the right grain, and the reasoning is recorded so it is not
relitigated.** The risk lives in the act, not the account: "send an email as me"
is equally consequential from any mailbox. Per-mailbox would also open a hole
this design does not have — add a third mailbox a year later and it acts under a
default nobody chose. And the coarse control already exists at the per-mailbox
grain: the SMTP opt-in decides *whether* Lumogis may send from a mailbox at all.

**Capability is per-mailbox; autonomy is per-connector.** Those two dials are at
different grains on purpose. A third, per-mailbox autonomy dial would overlap
the first, and three overlapping controls is how someone stops being sure what
is actually on.

**A gap found while checking this**, recorded rather than left for someone to
trip over: `list_my_permissions` builds its rows from the **action** registry
alone (`connector_permissions.py:117-129`), while the `PUT` validates against
the action registry **or** the capability registry (`:153-200`). A
`capability.{id}` mode is therefore settable but never listed.

**And it is not merely unlisted — it is enforced.** `execute_capability_http`
calls `may_execute(connector="capability.{id}", …)`
(`services/execution.py:280`) → `check_permission` (`:120-122`) →
`get_connector_mode` (`permissions.py:154-158`). With no explicit row the lazy
default is `ASK`, and `may_execute` passes no `approved`, so **every capability
write tool is denied by default from a setting no surface lists** — and the deny
payload hints `PUT /api/v1/me/permissions/{connector}` (`execution.py:334`), a
route the product does not expose. `/me/tools-capabilities` shows the
consequence as `Permissions: blocked` (`unified_tools.py:390-392`) — a wall,
where the truth is *"it would ask you first."* Latent only because
`CAPABILITY_SERVICE_URLS` is unset by default (`main.py:947-969`); it goes live
with the first configured capability service.

**Correction (2026-08-25).** As first written, this decision claimed the second
occupant "already ships on a different page" — that `/me/tools-capabilities`
renders capability scope grants via `GET /me/capability-grants`. All three parts
of that were wrong: there is no `/me/capability-grants` (the route is
`GET /api/v1/me/permissions/capabilities`, a **sub-path of the permissions
router** — the backend already converged); `/me/tools-capabilities` calls
`GET /api/v1/me/tools` and never touches the grants route; and
`list_my_capability_grants` has **zero web consumers**. The keep-the-page
conclusion survives the correction and is strengthened by it — the second
occupant is not merely real but already enforcing — but the convergence
question the paragraph deferred has now been answered. See
`.cursor/explorations/LUM-849-autonomy_surface.md` and
`.cursor/adrs/autonomy_surface.md`: **converge the list, keep the layers
separate.**

**The two rows stay.** Verified against the live action registry: every `imap`
action is **reversible** and private to your own mailbox (save draft, flag,
label, mark read, move); every `smtp` action is **irreversible** and received by
someone else (compose, reply, forward, unsubscribe). That is the line between
tidying your own mailbox and sending something a person receives — two honestly
different questions.

## Alternatives considered

**Deriving field presence by decrypting on read** — the exploration's
recommendation. Rejected: it adds a decrypt to the enumeration path (ADR-199),
inherits its failure mode, and cannot describe an unconfigured connector.

**Extending `delivery_paused`** — rejected per decision 1.

**A schema-driven form rendering every connector from the registry.** Attractive
— it would have killed LUM-846's bug class structurally, since that bug existed
in four places because there were four hand-written submit paths. Rejected on
evidence: `{name, label, is_secret}` cannot express `ImapForm`'s injected
constants, `NtfyForm`'s topic regex and optional-secret semantics,
`LlmApiKeyForm`'s minLength, or `PaperlessForm`'s trim-on-submit rules. Covering
those needs `type / required / pattern / min / max / const / default` — a form
DSL, which is a larger project than this chunk. `fields` therefore ships as
display metadata and rendering stays hand-written.

**An admin mirror of the toggle.** Rejected: `enabled` is a per-user column, and
the household/system tables have none. The card gates field presence off those
two tiers for the same reason.

## Consequences

1. **Disabled and absent are indistinguishable by exception type** — only by
   message, and not even that on the caldav and ntfy paths. Anything that needs
   to tell them apart must call `is_disabled`, which is why the tier walk does.
2. **A third copy of per-connector field knowledge now exists** — the registry
   schema, each loader's `_PAYLOAD_KEYS`, and the typed web form. All three are
   pinned equal by tests rather than by comments; the registry-as-source
   collapse is recorded as a follow-up, not adopted here.
3. **Read models were not gated, and that is a known class with three members** —
   `me_notifications` shows a disabled ntfy as the active channel,
   `/v1/models` keeps offering a model whose only credential is disabled, and
   `me_llm_providers` reports `status="configured"` for a switched-off row. The
   gate stops *use*; it does not by itself fix every surface that *advertises*.
   Each carries a follow-up. **`/v1/models` is not a one-line filter**: a
   disabled user row stops the tier walk, so a household key must not rescue
   the listing either.
4. **The card cannot say "you are using a household credential"**, because
   `ConnectorCredentialPublic` carries no such signal and
   `/me/connector-credentials` returns only the caller's own rows. G3's promise
   is user-tier-only, and the card renders "Not configured" for a member served
   by a household credential. Recorded rather than smoothed over.
5. **`paperless_configured` on the admin settings screen now reports `false`
   for a disabled credential**, and deliberately disagrees with the connectors
   card. That flag answers "can ingest run?"; the card answers "is my
   credential still stored?". Both are true; they are different questions.
6. **`register()` no longer drops `ConnectorSpec` extensions.** It rebuilt the
   spec from `(name, description)` and had been silently dropping
   `multi_instance` — a latent bug that would have become a visible one the
   moment a plugin connector tried to declare a field schema.
7. **The test doubles had to be taught the predicate, not the behaviour.** Every
   fake store initially applied the `enabled` filter unconditionally, which put
   the gate *inside the fakes*: deleting `AND enabled` from production left the
   whole suite green. They now apply it only when the statement carries the
   predicate. This is the general lesson — a fake that enforces the invariant
   itself cannot detect the invariant being removed.
