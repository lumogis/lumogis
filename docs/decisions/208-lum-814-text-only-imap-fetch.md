# ADR 208: Text-only IMAP fetch when the whole message exceeds the body cap
**Status:** Finalised
**Created:** 2026-08-07
**Last updated:** 2026-08-07
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-08-07 (composer)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/lum_814_text_only_fetch_retro.md`
**Draft mirror:** `.cursor/adrs/lum_814_text_only_fetch.md`

## Context

LUM-810 introduced `fetch_body_bounded` with a whole-message `BODY.PEEK[]` path capped at `_MAX_BODY_BYTES`. A one-line email with a large photo attachment was refused as "too large to show here" because the cap measured RFC822 bytes including non-text parts. **LUM-814** fixes the aim of the cap: text readability must not be blocked by attachment weight on the same message.

## Decision

When the whole-message fetch would exceed `_MAX_BODY_BYTES` (or the server withheld `RFC822.SIZE` and the fetched blob is oversized), `EmailImapSource` attempts a **text-only path**:

1. Fetch `BODYSTRUCTURE` once per read (shared with the inline-image walker on the oversized path).
2. Walk the structure with `_text_part_specs` to enumerate TEXT parts by RFC 3501 part number and encoded size.
3. If the sum of TEXT part sizes is within `_MAX_BODY_BYTES`, fetch each part via `BODY.PEEK[n.MIME]` + `BODY.PEEK[n]`, assemble MIME entities, and extract text through `services/email_body.text_from_parts` (plain preferred over HTML — shared with the whole-message extractor).
4. If any step fails or the TEXT alone is oversized, return `None` from `_fetch_text_only` and **fall back** to the pre-LUM-814 outcomes (`too_large`, whole-message path, or prior reason mapping) — never a new failure mode from partial fetch alone.

This is **not** the byte-range partial `BODY.PEEK[]<0.N>` approach rejected in the LUM-810 spike (mid-MIME truncation on a different response key).

### As-implemented surface

- `orchestrator/adapters/email_imap_source.py` — `_text_part_specs`, `_bodystructure`, `_fetch_text_only`; integrated in `fetch_body_bounded`.
- `orchestrator/services/email_body.py` — `text_from_parts` shared helper.
- `orchestrator/tests/_fakes.py` — structure/body derived from the same bytes; `message/rfc822` numbering corrected.
- `orchestrator/tests/test_email_body_fetch.py` — oversized refusal via withheld BODYSTRUCTURE.

## Alternatives considered

- **Raise the byte cap** — rejected; would transfer large attachments on every read and weaken the bounded-read contract from LUM-810.
- **Byte-range partial fetch** — rejected in LUM-810 spike; truncates raw MIME mid-part.
- **New plaintext host column / separate store** — not applicable; problem is fetch scope, not metadata location.

## Consequences

- Users can read the text of messages whose total RFC822 size exceeds the cap when TEXT parts alone do not.
- LUM-713 reuses the same `BODYSTRUCTURE` session on the oversized path for inline images.
- Tests must model BODYSTRUCTURE accurately; the shared fake now derives it from message bytes so walkers cannot pass against inconsistent fixtures.
- Servers that omit BODYSTRUCTURE on large messages still hit the legacy refusal path — acceptable until live evidence says otherwise.

## Revisit conditions

- Persistent `too_large` on small apparent text with captured BODYSTRUCTURE → extend the walker, keep fallback.
- Evidence that major providers omit BODYSTRUCTURE on oversized UIDs → evaluate a second strategy; do not remove fallback.

## Linear linkage (Product OS)

- **LUM-814** — closed **Done** 2026-08-07 via `/linear-update` + this retrospective.
- No new backlog item required.

## Testing retrospective

- Unit tests in `test_email_body_fetch.py`; fake fidelity in `_fakes.py`.
- Post-merge full orchestrator pytest: **5034 passed** (`dev` @ `fac2dc124`).
- Live IMAP verification remains **LUM-811** (`gate:pre-main`).

## Status history

- 2026-08-07: Finalised by /record-retro (retrospective). Shipped in `7bf477fff`.
