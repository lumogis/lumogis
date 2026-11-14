# ADR-196: OAuth refresh failures carry their kind

**Status:** Accepted
**Created:** 2026-07-31
**Last updated:** 2026-07-31
**Decided by:** `/explore LUM-757` (Opus 5)
**Linear:** [LUM-757](https://linear.app/lumogis/issue/LUM-757/oauthrefresherror-cant-tell-a-revoked-grant-from-a-token-endpoint)
**Relates:** ADR 195 (LUM-633 poll health), ADR 190 (OAuth2 provider connect)

## Context

ADR 195 built a four-code mailbox-health taxonomy — `credential` | `egress_blocked` | `network` | `config` — so the health banner would stop telling household members to fix credentials that were fine. It reached the IMAP-connect path and not the OAuth-refresh path.

`OAuthRefreshError` is raised for ten distinct conditions in `refresh_access_token`, from "the operator never configured a client id" to "the token endpoint timed out" to "the grant was revoked". All ten subclass `CredentialUnavailable` → `RuntimeError`, and `_classify_construction_failure` maps every one to `credential`. The banner then says *"Lumogis can't sign in to this mailbox. Reconnect it…"* — so a user re-runs a device-code flow to fix a network blip, or to fix an operator's missing environment variable.

The message cannot be used to tell them apart: by deliberate design (`email_oauth.py:23`) it carries only a status code and provider name, never the response body. Classification has to happen where the response is still in scope.

## Decision

Give `OAuthRefreshError` a **`kind`** attribute, set at the raise site, and have the health classifier read it.

**`kind` takes the health taxonomy's own values — `"credential"` | `"network"` | `"config"` | `"egress_blocked"` — the full four, not a new parallel vocabulary, and not the two values (`revoked` / `network`) the issue proposed.** Two values cannot express `no client_id configured`, which is an operator problem currently rendered to a household member as "reconnect your mailbox" — the same harm this work exists to remove, in a place the issue did not name.

The non-2xx branch classifies on the response's RFC 6749 `error` field and status:

| Response | `kind` |
|---|---|
| 400 `invalid_grant` (revoked / expired / password change / inactivity) | `credential` |
| 400/401 `invalid_client` (wrong or unregistered client id) | `config` |
| 429, 5xx | `network` |
| **egress-guard block** (`EgressBlockedError`) | **`egress_blocked`** |
| connect failure / timeout | `network` |
| non-JSON body **on a 2xx** (captive portal) | `network` |
| missing payload / missing refresh_token | `credential` |
| unknown provider / no client_id / no access_token in body | `config` |

`kind` defaults to `"credential"`, so any raise site that misses it degrades to today's behaviour rather than to something new.

This mirrors `ImapConnectError.kind` in the same lane, whose docstring already settled the principle: the kind is decided at the wrap site, not by the caller, because making the classifier inspect `__cause__` pushes provider knowledge into it.

## Alternatives Considered

* **Exception subclasses per kind** — mildly favoured by general practice over a discriminator attribute, and back-compatible since subclasses inherit the base. Rejected because it puts three new types into two classifiers that ADR 195 requires to stay in agreement, against an in-repo precedent that solved the identical problem with one type and a `kind`.
* **Classify by inspecting `__cause__` in `_classify_construction_failure`** — already rejected for the neighbouring IMAP case, and impossible here: the discriminator is the response body, which is deliberately not attached.
* **Return a result object rather than raising** — a signature change across several call sites for a labelling fix.
* **Retry transient failures in place** (backoff honouring `Retry-After`, short negative cache) — a real improvement, deferred. The poll is already scheduled, so `network` plus the next tick is sufficient; bundling a retry policy into a classification fix hides it.

Full detail: `.cursor/explorations/LUM-757-oauth-refresh-failure-kinds.md`.

## Consequences

**Easier**

* A blocked or flaky token endpoint reads as "usually clears up on its own" instead of demanding a device-code flow.
* An unconfigured `LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID` reads as a configuration problem — pointing at the operator, who can fix it, rather than the household member, who cannot.
* LUM-688's live proof gains something worth asserting: both a revoked grant and an unreachable token endpoint are producible against a real provider.

**Harder**

* Two classifiers must stay in agreement (ADR 195's standing rule). This adds a type that only one of them can observe, so the other needs the branch or an explicit comment saying why not.
* **`kind` has three consumers, not one** — the health classifier, the SMTP handler's error code, and `routes/signals.py:523`, which still maps any refresh failure to `422 imap_not_configured` (tracked as a follow-up). A new value must be considered against all three; an unrecognised one falls to `credential`, which is the safe direction.
* ~~**The send path's permanence is load-bearing and must not be "fixed".**~~ **Resolved structurally by LUM-762 (2026-08-03).** This bullet was the *only* thing standing between the codebase and a duplicate outgoing email, and it was enforced by a comment plus one parametrised test — which was not enough, since a competent reviewer proposed the retry a second time and LUM-762 was filed to record why. The hazard is now closed by construction: `fail_execution` and its `executing → approved` edge are deleted, every failure dead-letters on the first attempt, and `_CLAIM_BY_ID_SQL` filters `run_after`. There is no zombie row for an executor to redrive, because none can be created. **`permanent` still matters — for the error *code* the composer renders, not the disposal.** See `docs/decisions/201-lum-762-action-proposals-claim-not-queue.md`.
* A `kind` attribute is weaker than distinct types: nothing forces a new raise site to set one. The default and a raise-site-enumerating test are the mitigation, not a guarantee.

**Future chunks must know**

* **Key on RFC 6749 `error` values, never on provider strings.** `invalid_grant` and `invalid_client` are standard; `AADSTS50173` is Microsoft-only and would not survive LUM-689 adding Gmail.
* **The message stays status + provider only.** The body may be read to classify; it must not reach the message or the logs.
* The taxonomy is ADR 195's. A new health code belongs there first, not invented here.

## Revisit conditions

* **A second OAuth provider ships** (LUM-689) → confirm the `error`-value mapping holds without provider-specific branches.
* **A third discriminator is needed** (e.g. distinguishing "revoked" from "expired" for different copy) → revisit attribute vs subclasses; three values is where an attribute stops obviously winning.
* **Transient refresh failures become a real availability problem** → implement in-place retry with `Retry-After`, and revisit whether `network` should suppress the banner until N consecutive failures.
* **ADR 195's taxonomy changes** → this mapping changes with it; the two are not independent.

## Status history

- 2026-07-31: **Accepted on verification of LUM-757.** Implementation confirmed the decision: four kinds set at eleven raise sites, read by two consumers, 42 tests, two mutation checks on the production paths plus one on the AST guard. Draft retained at `.cursor/adrs/LUM-757-oauth-refresh-failure-kinds.md`.

- 2026-07-31: **Revised during `/review-plan --arbitrate` R1.** Two independent critics found the same P1: `egress_blocked` was missing. `oauth_egress_scope` turns a tethered block into `EgressBlockedError`, which falls into `refresh_access_token`'s blanket `except Exception` and would have been labelled `network` — telling a household member "usually clears up on its own" for a condition that needs an operator to edit an allowlist and restart Core, while `feed_monitor.py:964` already classifies that type as `egress_blocked`. Three kinds became four. **Also reversed:** a self-review addition that would have made `network` refresh failures retryable on the SMTP send path. `proposal_queue.claim_next` has no production callers, `_CLAIM_BY_ID_SQL` ignores `run_after`, and the composer already assumes the row is dead and builds a fresh proposal — so the change would have left zombie `approved` rows and, once a background worker exists, sent duplicate emails. The send path keeps `PermanentActionError` and changes only its **code**.

- 2026-07-31: Draft created by `/explore` LUM-757. Registered as blocking LUM-688 (🟡) and LUM-689 (🟡).
