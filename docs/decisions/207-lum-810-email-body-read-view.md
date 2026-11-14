# ADR-207: Reading an email means extracting its text, not rendering its HTML

**Status:** Accepted
**Created:** 2026-08-06
**Last updated:** 2026-08-07
**Decided by:** `/explore LUM-810`, finalised by `/verify-plan`
**Linear:** LUM-810 (blocks LUM-713, widens LUM-704)
**Exploration:** `.cursor/explorations/LUM-810-email-body-read-view.md`
**Plan:** `.cursor/plans/LUM-810-email-body-read-view.plan.md`
**Draft mirror:** `.cursor/adrs/email-body-read-view.md`
**Relates:** ADR-202 (`RecoverableSends` — the read-only body presentation this reuses), ADR-206 (confirms this is core, not premium), LUM-698 (the quote route this deliberately does not reuse), LUM-719 (the shared-signal precedent)

## Context

There is no way to read an email in Lumogis. The card shows an LLM summary; the user is asked to triage on it. The body is fetchable — `email_quote.fetch_body_text` does a bounded, ownership-gated IMAP fetch — but is only wired into the composer's collapsed quote disclosure. Today the only way to read your mail is to press Reply.

Three findings shape the decision, none of them in the ticket.

**`POST /email/quote` is composer-shaped.** It returns `> `-prefixed text with an "On … wrote:" attribution. Reusing it would show the user their own mail as a quotation of itself.

**The body extractor only understands `text/plain`.** `_body_to_text` walks multiparts collecting `text/plain` only. HTML-only multipart mail — very common — yields an **empty body**; HTML-only single-part yields **raw markup as text**. The LLM summary for those messages is built on nothing.

**Nothing sanitises control characters.** `safety_playground.py` carries a deliberate U+202E Trojan-Source payload as a scanning fixture, but no displayed text anywhere is checked for bidi overrides. "Plain text is safe" is false: BiDi override and homoglyph attacks work in plain text, and mainstream clients remain vulnerable.

## Decision

**A read view serves extracted text. Lumogis does not render email HTML.**

- **New `POST /api/v1/email/body`**, beside `/quote`, with the same `require_user` + `_assert_owned_signal` posture (404 not 403 — no oracle) and **its own** rate bucket, per the existing rule that a bounded IMAP fetch must not consume the LLM budgets.
- **Extraction:** prefer `text/plain`; when absent, down-convert the `text/html` part with a **stdlib `html.parser` block-aware extractor** — newline at block boundaries, tab between table cells, `script`/`style` dropped. **No new dependency, not even a transitive one promoted to direct.**
- **trafilatura is explicitly rejected for this path** (spike, 2026-08-06). Its `MIN_EXTRACTED_SIZE=250` discards short replies entirely, and with thresholds lowered it silently drops the *first sentence* of a two-line reply — a truncated message the reader cannot tell is truncated. It stays in use for web signal extraction, where the input really is an article.
- **Control characters are marked, never silently stripped.** **Bidi overrides and isolates** (U+202A–U+202E, U+2066–U+2069) are made visible. Email is someone's actual message; silently altering it is its own failure mode. Gitea took the same warn-and-mark route for Trojan Source.
- **Zero-width characters are deliberately NOT annotated** (U+200B–U+200D, U+FEFF). ZWJ is structural in ordinary emoji sequences and ZWNJ is orthographically required in Persian, so blanket annotation renders a family emoji or a Farsi sentence as sentinel soup — inverting the faithfulness principle above in the name of upholding it, and training users to ignore the sentinel so the one real U+202E carries no signal. Invisible-text steganography via ZWSP/BOM is a real concern but a **prompt-boundary** one, not a display one; it is recorded against LUM-704 rather than answered here. Homoglyphs are likewise out of scope for this decision — per-script confusability is not a control-character problem.
- **Presentation reuses `RecoverableSends.tsx`** — the `BODY_PREVIEW_CHARS` truncate-and-expand pattern already shipped for failed sends (ADR-202).
- **No read affordance on household-shared signals.** A non-owner cannot fetch the body and never will. Offering an action that can never work was filed as a bug once already (LUM-719).

## Alternatives Considered

- **Sanitised HTML rendering (DOMPurify + strict CSP)** — what users implicitly expect, and the only option that unblocks LUM-713 as written. But it is a security decision wearing a feature's clothes: remote images are tracking pixels and a sanitiser bypass is stored XSS on our own origin. Deferred to its own ticket and its own review.
- **Reusing `/quote`** — returns a quotation, not a message. Rejected.
- **Sandboxed iframe on a separate origin** — the correct destination if Lumogis ever renders HTML mail, and already named in LUM-713 as acceptable containment. Disproportionate as the way to first let someone read a message.

## Consequences

**Easier**
- Users can verify what they are triaging without leaving for Gmail, which is the premise the whole surface rests on.
- Fixes an unfiled defect: HTML-only mail currently produces a blank or garbage body.
- **No sender-controlled *markup* enters a rendered HTML context**, so the containment argument LUM-713 demands is not needed — it is avoided rather than answered. *(2026-08-07, amended when LUM-713 shipped: sender-controlled **bytes** now do reach an `<img src="data:…">`, which the original wording ruled out too broadly. The property that actually carries the argument is narrower and still holds — nothing sender-authored is ever **parsed as markup or executed**. The strip keeps it by identifying every image from its own binary signature against a four-format allowlist and refusing anything else, which is what excludes SVG: it is the one image format that is also a script container, and it has no magic number, so only an allowlist can exclude it.)*
- Fetch, ownership gate, rate-limit shape, and presentation all already exist. The extractor is new but is ~40 lines of stdlib with no dependency — small enough to read in one sitting, which matters for a function handling untrusted input.

**Harder / foreclosed**
- Commits the surface to **text-only** reading. Newsletters read as flattened text. This is a real limitation and should be stated in the UI, not discovered.
- **LUM-713 becomes unimplementable as written** — with no rendered HTML context there is nowhere for a `cid:` image to appear. *(2026-08-07: that prior decision was taken — LUM-815, no. LUM-713 is re-scoped onto showing inline images **below** the text, which needs no rendered context. Its intent survived; only its method did not.)*
- **LUM-704's surface widens** before it ships: HTML-only bodies that are empty today would start carrying sender text into LLM prompts.
- **LUM-630 changes behaviour**: body-keyword rules would begin matching messages whose bodies were previously empty, silently reclassifying existing mail.

**Future chunks must know**
- Whether extraction also changes what the **classifier** sees is deliberately unsettled here. Changing ingestion reclassifies existing mail; not changing it means the summary and the visible body can disagree.

## Revisit conditions

- ~~If the trafilatura spike shows…~~ **Resolved 2026-08-06:** the spike ran; trafilatura scored 71–79% recall against 100% for a stdlib block-aware extractor, and its failure mode is silent partial truncation. Extractor changed; the decision to extract rather than render is unaffected. Revisit only if a fixture appears that the stdlib walker mishandles.
- ~~If HTML rendering is ever taken on…~~ **Resolved 2026-08-07 (LUM-815): it is not.** The text path is the default and there is no fallback to revisit. Reopen only if real usage shows a class of mail that is genuinely unusable as text — the six-fixture corpus makes that cheap to test.
- If a future surface needs to render mail content for any reason, the separate-origin option from LUM-713 is the shape to reach for, not client-side sanitisation bolted onto this route.

## Status history
- 2026-08-06: Draft created by `/explore LUM-810`.
- 2026-08-06: Spike run. Extractor changed from trafilatura to a stdlib `html.parser` block-aware walker — 100% vs 71–79% recall on a six-fixture email corpus. Core decision (extract, do not render) unchanged.
- 2026-08-06: **Revised.** The control-character clause said bidi *and zero-width* are made visible; plan review (arbitration rounds 1–3) settled that zero-width must be left unmarked, and the ADR had drifted from the plan for three rounds. Scope of annotation narrowed to bidi only, with the zero-width exclusion and its two out-of-scope neighbours (prompt-boundary steganography, homoglyphs) stated as decisions rather than omissions. The extract-don't-render decision is unaffected.
- 2026-08-06: **Finalised by `/verify-plan`** — implementation confirmed the decision. Shipped as `orchestrator/services/email_body.py` (stdlib `html.parser` walker, no new dependency), `EmailImapSource.fetch_body_bounded`, `POST /api/v1/email/body`, and `EmailBody.tsx`. Two consequences the ADR predicted were **measured** rather than assumed: HTML-only mail did yield an empty body before this (pinned by `test_extract_when_html_only_multipart_then_yields_text_not_empty`), and the classifier now sees that text because `_body_to_text` delegates to the same extractor. One decision was **sharpened** during implementation: the whole-message size precheck degrades OPEN when a server withholds `RFC822.SIZE`, so a post-fetch byte check was added — the ADR's "bounded input" claim was true only for servers that answer.
- 2026-08-07: **Revisit condition answered — the decision stands.** LUM-815 asked whether Lumogis should render HTML mail. Evaluated against this extractor as shipped rather than argued from first principles: receipts and letters (nested layout tables, bold dates, bullet lists, links) come through **complete** — every price, date and URL preserved. Only marketing and editorial mail degrades, losing column grouping and photos, and that is the mail a user least needs to read accurately before triaging. HTML rendering would buy the most where reading accuracy matters least, at unchanged cost. **Closed no.** The evaluation did surface a real gap this ADR did not anticipate: `cid:` inline parts are visible *nowhere* — excluded from the attachment list as "not a file the user received", with no rendered body to appear in. That is answered by showing inline images **below** the extracted text, which needs no rendered HTML context; LUM-713 is re-scoped onto it.

- 2026-08-07: **Consequence amended by `/verify-plan` — LUM-713 shipped.** The Consequences section claimed *"no sender-controlled bytes enter a rendered HTML context"*, and the inline image strip makes that literally false: base64 image bytes now reach an `<img src="data:…">`. The wording was broader than the argument it was carrying. The property that actually does the work is narrower and still holds — **nothing sender-authored is parsed as markup or executed** — and the strip preserves it by identifying every image from its own binary signature against a four-format allowlist, which is what excludes SVG. The decision itself is unchanged: Lumogis still renders no sender HTML. Recorded rather than quietly corrected, because a consequence that stops being true is exactly what a later reader would rely on.
