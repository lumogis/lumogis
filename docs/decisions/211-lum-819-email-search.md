# ADR-211: Email search stores the extracted message text and searches it locally

**Status:** Accepted
**Created:** 2026-08-10
**Last updated:** 2026-08-10
**Decided by:** `/explore LUM-819`, revised through two critique rounds, decided by Thomas, finalised by `/verify-plan`
**Linear:** LUM-819 (blocks LUM-173, LUM-89; children LUM-824, LUM-825)
**Exploration:** `.cursor/explorations/LUM-819-email-search.md`
**Plan:** `.cursor/plans/LUM-819-email-search.plan.md`
**Draft mirror:** `.cursor/adrs/email-search.md`
**Relates:** ADR-207 (the read view whose owner-only posture this inherits), ADR-206 (confirms this is core — it reads no graph), ADR-205 (base email is core and AGPL), migration `041` (the `memories` FTS pattern this follows)

> **This ADR reverses its own first draft.** The 2026-08-09 version decided email search would cover subject, sender and summary only, and would *not* store message text. That was wrong. The reversal is recorded rather than overwritten because the same reasoning is easy to reach for again.

## Context

The email surface had no search. The triage lanes are organised around what to do next, so a message already dealt with was reachable only by scrolling.

Search is expected to cover message text. Every client users come from — Gmail, Outlook, Apple Mail — searches the body. A search box that silently omits it does not read as scoped; it reads as broken.

**Bodies were not stored.** `extract_text` ran at ingest, fed the classifier, and its output was dropped — `adapters/email_imap_source.py` said so in terms: *"`fetch_bodies` calls `_body_to_text(raw)` and discards `raw`"*. What persisted was subject, sender, and an LLM summary that in metadata mode is a copy of the subject.

**What does persist, persists indefinitely.** No `DELETE FROM signals`, no purge job, no TTL. A message triaged three months ago still has its row, which is what makes a local index worth building.

### Why the first draft was wrong

It ruled out storing body text on three grounds. Two did not survive checking:

1. **"It reverses a deliberate decision."** It does not. `raw_content: str  # transient` is processing hygiene, and ADR-207 — the only ADR that could have decided this — covers extract-vs-render for the read view and says nothing about storage. An absence was cited as a decision.
2. **"It reproduces Thunderbird gloda."** Wrong granularity: gloda stores whole message copies; this stores extracted plain text. For 20k messages that is ~39 MB of text against the ~59 MB of dense vectors Lumogis **already** stores for the same corpus.
3. **"Metadata mode exists so bodies need never be stored."** Holds — but only for metadata-mode mailboxes, which opt out. A full-ingest mailbox has already had every body fetched, extracted and passed through an LLM; storing the result is a smaller step than what already happens.

Precedent the first draft missed: `projection.py` stores extracted *document* text at rest, so an email's PDF attachment was already searchable (LUM-702) while the email's own body was not.

## Decision

**Lumogis stores the extracted plain text of ingested email and searches it locally, alongside subject and sender.**

- **Storage** — `email_body_text(signal_id PK → signals ON DELETE CASCADE, user_id, text, truncated, created_at, text_tsv)`, capped at `EMAIL_BODY_TEXT_MAX_BYTES` (64 KB). A separate table, not a column on `signals`, so the hot lane queries are untouched, retention and erasure are independent, opting out is simply *no row*, and non-email sources carry no `NULL`.
- **Indexing — two tools for two corpora.** `pg_trgm` GIN on `title`, `from_addr`, `from_display`, `content_summary` (short, identifier-like, typo-prone, substring-matched); a `GENERATED … STORED` `tsvector` GIN on the body text, following migration `041`'s `memories.content_tsv` pattern rather than an expression index.
- **The body index uses `'english'`, and every query token carries the `:*` prefix operator.** Measured across five options on Postgres 16, this is the only combination handling `invoice`↔"invoices", `plumb`→"plumbing", `the invoice` **and** `Rechnung`→"Rechnungen". **Prefix matching is what makes an English configuration safe for German mail** — it rescues exactly what the English stemmer cannot reduce — so the "multilingual mail needs `'simple'`" reasoning is superseded. Email shares the `'english'` configuration `memories` already uses rather than introducing a second.
- **Because the query is hand-built, the sanitiser is a security control.** Prefix matching cannot be expressed through `websearch_to_tsquery`, so raw `to_tsquery` is used and would raise on ordinary input. Query tokens are stripped of every non-word character — which covers every `tsquery` metacharacter — **with a Unicode-aware class**. An ASCII-only strip measurably breaks German search: `Müller` becomes `Mller` and returns nothing.
- **Body matches are owner-only.** Subject and sender may use the household union; the body half filters to the calling user, scoped **inside** the `LEFT JOIN LATERAL` so every downstream consumer — the match arm, the rank, the snippet, the counts — is owner-safe by construction. LUM-810 decided shared signals get no read affordance, and a body match on a shared signal would leak through search exactly what the read view refuses.
- **The surface reports what matched where.** Per-field counts (`All / Subject / Sender / Message`) accompany every query, and `fields` narrows afterwards. Search fails silently by nature — an empty list is indistinguishable from an absent message — and the counts are what make the remaining difference between the two engines legible. Bounded at 200; an exact count cannot run under the result `LIMIT`.
- **Existing mail is backfilled** by an operator-invoked job using **`fetch_body_bounded`** (LUM-810), not the poll-time `fetch_bodies`, which is INBOX-only and therefore cannot reach mail that LUM-645's organize has moved — i.e. most of the mail worth finding. Permanently-unfetchable candidates are recorded so a re-run terminates rather than rescanning its own failures.
- **Metadata mode stores nothing** and searches subject and sender only — **and this binds the backfill, not only ingest.** A backfill that ignores `extraction_method` reverses a user's stated opt-out retroactively across their whole mail history, which is a worse outcome than the feature not existing. It is a privacy control, pinned by a test that asserts no IMAP connection is opened at all.

**No IMAP `SEARCH` pass-through.** Its remaining justification — mail Lumogis never ingested — moved to LUM-824 and LUM-825, where disclosure and backfill are better answers than a per-query provider call.

## Alternatives Considered

- **Subject + sender + summary only** *(this ADR's own first draft)* — cheap and honest about scope, but does not do what search is for. Superseded.
- **IMAP `SEARCH` pass-through** — the only route to body text without storing it, but *"BODY and TEXT searches may be slow on large mailboxes because the server scans full message text"*, Gmail rewrites IMAP search into its native engine, and it is unavailable offline.
- **`'simple'` configuration** — chosen first, on the argument that English stemming mangles German. Measurement killed it: with `:*`, `'english'` matches `Rechnung`→"Rechnungen" that `'simple'` also matches, *and* handles `invoices`→"invoice" that `'simple'` misses.
- **Trigram over the body as well** — the only option making both halves substring-match, but the largest index in the plan, and it still misses `2400` against "€2,400.00" while breaking multi-word queries into literal-phrase matches.
- **Postgres FTS over everything** — right for the body, wrong for subject and sender, where it cannot substring-match; hence the split.
- **Storing raw MIME** — the gloda failure. Extracted text only.
- **Elasticsearch / Solr / Xapian / notmuch** — a second stateful service, or a Maildir Lumogis does not have.

## Consequences

**Easier**
- Search does what users expect, offline, with no provider round trip and no per-provider behaviour to normalise.
- The stored text is **already computed** — one write, not a new fetch or parse.
- Retention is unbounded, so "find what I triaged months ago" works without a windowing caveat.
- A separate table makes erasure (`ON DELETE CASCADE`), export (LUM-188) and opt-out mechanical rather than conditional.
- Semantic email search becomes possible later, because a real text corpus now exists.

**Harder / foreclosed**
- **Mail text is now data at rest.** Smaller than the embeddings already stored and smaller than what the ingest LLM already sees, but a new category in backups, in the export, and in any future disclosure. This is the cost of the decision and should not be described as free.
- ~~**Quoted reply chains are stored whole**, measured at ~8.6× amplification, so one phrase can match every later message in a thread.~~ **Resolved 2026-08-13 (LUM-826).** Migration `075` adds `email_body_text.text_stripped` and re-points the generated vector at `to_tsvector('english', COALESCE(text_stripped, text))`; `services/email_quote_strip.py` is its only writer. On a synthetic four-message thread the index went from **4 hits to 1** for a phrase written once (asserted both ways in `test_migration_075_stripped_index.py`), and stored-bytes amplification from **2.93× to 0.98×**.
  **That ratio is not comparable to the original 8.6×** — that figure came from a real corpus which I do not have, so this is a synthetic thread measured end-to-end, not the same measurement re-run. Re-measure on real mail before treating the storage arithmetic as settled.
  Three deliberate limits: the full `text` is **kept**, so a better stripper can be re-run rather than having destroyed the original; `text_stripped IS NULL` means *"the stripper declined"* and indexes the original, which is what keeps a wholly-quoted forward findable; and rows written before `075` stay `NULL` — unchanged, not half-migrated — until `python -m services.email_body_backfill --restrip`, which is local-only and never touches IMAP.
- ~~**`2400` does not find "€2,400.00"** under any `tsvector` configuration; the text search tokenises it as `2` and `400.00`. Fixing it needs a normalised digits-only copy, which is a different mechanism.~~ **Resolved 2026-08-14 (LUM-827).** Migration `078` adds `email_body_text.digits_tsv`, a **generated** `to_tsvector('simple', …)` over a digits-only reduction of the same `COALESCE(text_stripped, text)` the word index reads, matched with the `:*` prefix operator. The word index still misses the amount — that limit was never the fixable part, and it is still asserted — so the numeric index is a second arm OR-ed into the body predicate, not a replacement.

  **The ticket's proposed mechanism was measured and rejected.** LUM-827 suggested stripping every non-digit from the whole document and substring-matching. For `Call me on 07700 900024. Invoice 00 attached. Total EUR 2,400.00 due in 30 days.` that yields `077009000240024000030`, which contains `90002400` — a string present in **neither** the phone number nor the invoice, manufactured by butting two unrelated numbers together. Every such artefact is a silent false positive, and search that returns confident wrong answers is worse than search that returns none.

  What ships instead normalises **per numeric token**: separators *between* digits are joined (`2,400.00` → `240000`, `1,234,567` → `1234567`), everything else breaks the token, and one-digit tokens are dropped. **One case stays genuinely ambiguous and is resolved rather than hidden:** `12,13` is a German or French decimal *and* a space-less English list, so `weeks 12,13,14` indexes as `121314` — `13` misses it, `1213` finds it. The decimal reading is chosen because indexing German mail is a first concern of this ADR, and because a list written the ordinary way (`12, 13, 14`) never reaches the join at all: it requires a digit *immediately* after the separator. Pinned both ways in `test_migration_078_digits_index.py`. Ordinary whitespace is **not** a joiner — with `\s` in the class, `1000 1001 1002` became the single token `100010011002`, the same manufactured match — though U+00A0 (no-break) and U+202F (narrow no-break) are, since between digits they are the French and Russian thousands separator and essentially nothing else. Measured on Postgres 16.13 and asserted in `test_migration_078_digits_index.py`.

  Three deliberate limits. A **space-grouped** `12 34 56 78 90` indexes as five tokens, so a grouped IBAN is not findable whole; an IBAN in the wild carries a country prefix, and a query with letters never reaches this index. Matching is **prefix, not substring**, so `2400` finds `2,400.00` and does *not* find `124005` — substring would catch the second, and that is the trade keeping the false-positive rate honest. And a **query containing letters is not a numeric search**: `INV-2400` stays with the text index rather than matching every message that mentions 2400 in any context. The query side joins by a **mirror of the column's rule** and refuses anything reducing to more than one number, so `1 2` is not silently read as `12` — which would prefix-match `124005`, a message containing neither.

  **Generated, not application-written** — unlike `text_stripped`, whose quote detection is a heuristic. This is a pure regex, so SQL can express it, and that buys no backfill (every message already stored is searchable by number the moment the migration lands, which for a search feature is the difference between "works" and "works for mail received after Tuesday") and no drift between the stored value and the expression the query uses.
- **The language configuration is baked into stored data.** Changing it is an `ALTER` plus a full re-index. This is the chunk's one irreversible decision.
- **`"exact phrase"` and `-exclude` are given up** to buy prefix matching, since `websearch_to_tsquery` cannot express `:*`. Recoverable by prefixing only the final token.
- **An explicit Message affordance makes two existing limits visible** that were previously hidden inside a union — a metadata-mode mailbox shows `Message 0` permanently, and a household member's shared mail never matches on body. Neither is new and neither leaks, but both now require stating in the UI rather than merely being true.
- **First Postgres extension in the repo** (`pg_trgm`). Contrib, not third-party, and the official image runs `POSTGRES_USER` as a superuser. **Corrected 2026-08-13 (LUM-828) — the original wording here said an install whose role lacks `CREATE EXTENSION` fails to boot, which overstated the blast radius.** Measured on Postgres 16.13: `pg_trgm` is a **trusted** extension (PG13+), so superuser is *not* the requirement. The contract, each case asserted in `orchestrator/tests/test_migration_074_extension_privilege.py`:
  - a non-superuser that **owns** the database installs it fine — which is the shape managed Postgres normally hands you, so RDS / Cloud SQL / Supabase are **not** blocked;
  - only a role with **neither ownership nor `CREATE` on the database** fails, and that failure is still **orchestrator-down, not feature-off**, deliberately — a partial schema is worse than a refusal;
  - that role succeeds once an administrator has run `CREATE EXTENSION pg_trgm` once, because `IF NOT EXISTS` then short-circuits.

  **Decision on managed Postgres:** supported for the ordinary owner-role shape, with a documented one-statement pre-step for locked-down roles. **Degrading to a trigram-less schema stays rejected** — two schema shapes to reason about forever, plus a silently slower unindexed `ILIKE`, is a poor trade for a narrower configuration than this ADR originally believed. The migration now fails with the extension, the privilege, and the exact remedy in the message rather than a bare `permission denied`.
- Email becomes a **Postgres**-resident text corpus while documents and captures are **Qdrant**-resident. LUM-173 and LUM-89 must reconcile that; this decision deliberately does not.

**Future chunks must know**
- **Body matching is owner-only.** Any surface that widens it to the household union re-opens what LUM-810 closed.
- `content_summary` is **not** the body, and is a copy of the subject in metadata mode.
- Stored text is capped and truncation is recorded; do not assume a stored body is complete.
- **A literal `%` in SQL executed with a params tuple raises** — psycopg2 `%`-formats the query before Postgres sees it. `url LIKE 'imap://%%'` is doubled for that reason, and the unit lane cannot catch a regression here because the fake store never hands SQL to a driver. The live tests are what guard it.

## Revisit conditions

- **If a signal retention policy or TTL is introduced**, this decision's premise is void — the index silently stops covering the old mail it exists to find. The acceptance suite pins a 3-month-old triaged message so that change breaks a test rather than the feature.
- **If usage shows single-language mailboxes dominate**, re-measure the configuration; it is a re-index, not a config flip.
- **If body text materially outgrows the embeddings already stored**, re-measure the cap or a retention window — and account for quoted chains, which the original arithmetic did not.
- **If semantic email search is wanted**, revisit extending the Qdrant `signals` collection to hybrid; a text corpus now exists. Check ADR-206 first: it is core only if it does not read the graph.
- **If LUM-173 or LUM-89 lands a general multi-collection retrieval pattern**, reconsider whether email's text belongs in Postgres or should move to join it.
- **If a household ever needs shared-signal body search**, that is a reversal of LUM-810, not an extension of this — it needs its own decision.
- **If metadata mode is retired**, the subject/sender-only path loses its reason to exist and should be removed rather than left as dead capability.

## Status history

- 2026-08-09: Draft created by `/explore` LUM-819 — subject/sender/summary only, body storage ruled out.
- 2026-08-09: Revised — retention finding added; provider path reframed from "body text" to "everything not ingested".
- 2026-08-10: **Reversed.** Body search established as a product requirement; two of the three grounds for ruling out body storage did not survive checking.
- 2026-08-10: Revised during `/review-plan --arbitrate` (R1 sonnet-5 + R2 fable-5) — backfill primitive named (`fetch_body_bounded`), the metadata-mode promise stated to bind the backfill, and the two-engine divergence recorded as a consequence.
- 2026-08-10: **Decided (Thomas)** — `'english'` + prefix matching, and per-field match counts, after measuring five options on Postgres 16.
- 2026-08-10: **Accepted**, finalised by `/verify-plan` after implementation. Implementation confirmed the decision end to end on real Postgres and corrected one shipped defect the unit lane could not see: a literal `%` in a parameterised statement, which made every search 500.
