-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- LUM-827 — searching `2400` finds "EUR 2,400.00".
--
-- ADR-211 § Consequences shipped this as a known gap: the text search
-- tokenises "2,400.00" as `2` and `400.00`, so no query token matches, and no
-- `tsvector` configuration changes that. A user looking for the invoice they
-- know says 2400 gets an empty result, which is the single most confusing
-- thing a search box can do — indistinguishable from "the message is not here".
--
-- Same class covers reference numbers, order IDs and account numbers: anything
-- a human reads as ONE token and a tokeniser splits.
--
-- WHY NOT THE APPROACH THE TICKET PROPOSED. LUM-827 suggests stripping every
-- non-digit from the whole document and substring-matching the result.
-- Measured, that manufactures matches. For
--
--     Call me on 07700 900024. Invoice 00 attached. Total EUR 2,400.00 ...
--
-- it yields `077009000240024000030`, which contains `90002400` — a string
-- present in NEITHER the phone number nor the invoice, invented by butting two
-- unrelated numbers together. Every such artefact is a silent false positive,
-- and a search that confidently returns the wrong message is worse than one
-- that returns nothing.
--
-- WHAT THIS DOES INSTEAD — normalise PER NUMERIC TOKEN, never across the
-- document, in four passes measured on Postgres 16.13:
--
--   1+2. Join a separator that sits BETWEEN two digits: `2,400.00` -> `240000`,
--        `1,234,567` -> `1234567`. Two passes because a match consumes the
--        digits around it, so one pass leaves the second separator.
--   3.   Replace every other run of non-digits with a space, so digits from
--        two numbers separated by anything else can never merge.
--   4.   Drop one-digit tokens (`\m\d\M`). Every message contains a `1`
--        somewhere; indexing them inflates the index and makes one-digit
--        searches useless.
--
-- THE ONE CASE WHERE THAT IS AMBIGUOUS, stated because passes 1+2 cannot
-- resolve it and an unstated ambiguity reads as a bug. `12,13` is a German or
-- French DECIMAL — twelve point one three — and it is also how an English
-- writer might list two numbers with no space after the comma. This migration
-- reads it as one number, so `weeks 12,13,14` indexes as `121314`: searching
-- `13` does not find it, and `1213` does.
--
-- Chosen deliberately, not overlooked. Lumogis indexes German mail as a first
-- concern (ADR-211 turns on exactly that), where the decimal reading is simply
-- correct; and a list written the ordinary way, WITH a space after the comma
-- (`12, 13, 14`), never reaches this pass at all, because the join requires a
-- digit immediately after the separator. The residue is comma-lists written
-- without spaces, which is the typographic minority of an already narrow case.
-- Pinned by `test_a_comma_list_without_spaces_reads_as_one_number`.
--
-- ORDINARY WHITESPACE IS DELIBERATELY NOT A JOINER, and that was measured too:
-- with `\s` in the class, "1000 1001 1002" — a plain list of references —
-- became the single token `100010011002`, which is the same manufactured match
-- the whole-document strip was rejected for. Two numbers separated by a space
-- are the COMMON case in prose; a space inside one number is the rare one.
--
-- NO-BREAK space (U+00A0) and NARROW no-break space (U+202F) ARE joiners.
-- Between two digits they are the French and Russian thousands separator and
-- essentially nothing else, so `2<nbsp>400,00` is safely one number. They are
-- written as ARE `\uXXXX` escapes rather than literal bytes so this file stays
-- ASCII and the intent survives a careless editor.
--
-- The accepted cost: a space-grouped `12 34 56 78 90` indexes as five tokens,
-- so the whole string is not findable. An IBAN in the wild carries a country
-- prefix (`DE89 3704 ...`), and a query containing letters does not reach this
-- index at all — see services/email_digits.py. Narrow and correct beats wide
-- and wrong.
--
-- MATCHING IS PREFIX, via the `:*` operator the body index already uses:
-- `2400:*` matches the token `240000` (i.e. 2,400.00) and does NOT match
-- `124005`. Substring matching would catch the second; prefix does not, and
-- that is what keeps the false-positive rate honest while still finding the
-- amount somebody actually typed.
--
-- `simple`, not `english`: these are digit runs. Stemming them is meaningless
-- and the stop-word list could only remove tokens.
--
-- WHY A GENERATED COLUMN RATHER THAN A PYTHON-WRITTEN ONE. `text_stripped`
-- (migration 075) is Python-written because quote detection is a heuristic;
-- this is a pure regex, and expressing it in SQL buys two things Python cannot:
--
--   * NO BACKFILL. Every message already stored becomes searchable by number
--     the moment this migration lands. For a search feature that is the whole
--     difference between "works" and "works for mail received after Tuesday" —
--     and the ticket's complaint is precisely about mail you already have.
--   * NO DRIFT. A stored column written by application code can disagree with
--     the expression the query uses; a generated column cannot.
--
-- THE QUERY SIDE MUST JOIN BY THE SAME RULE. `services/email_digits.py`
-- normalises the user's query with a mirror of passes 1+2 and refuses a query
-- that reduces to more than one number, so `1 2` is not silently turned into
-- `12` — which would prefix-match `124005`, a message containing neither.
-- Kept in step by `test_the_query_joins_by_the_same_rule_as_the_column`.
--
-- COALESCE(text_stripped, text) MIRRORS `text_tsv` EXACTLY (migration 075). If
-- the two ever drift, a number quoted from an earlier reply becomes findable in
-- every message that quotes it — the amplification LUM-826 exists to remove.
--
-- NO explicit BEGIN/COMMIT: db_migrations.py runs each file inside one
-- transaction and commits it together with the schema_migrations row.
--
-- COST: adding a STORED generated column rewrites the table and builds a GIN
-- index. At household scale (tens of thousands of rows) that is seconds. It is
-- not an online migration and is not claimed to be.
--
-- Rollback (manual; the runner is apply-only / filename-tracked):
--   DROP INDEX IF EXISTS email_body_text_digits_idx;
--   ALTER TABLE email_body_text DROP COLUMN digits_tsv;

ALTER TABLE email_body_text
    ADD COLUMN IF NOT EXISTS digits_tsv tsvector
    GENERATED ALWAYS AS (
        to_tsvector(
            'simple',
            regexp_replace(
                regexp_replace(
                    regexp_replace(
                        regexp_replace(
                            COALESCE(text_stripped, text),
                            '(\d)[.,\u00a0\u202f](\d)', '\1\2', 'g'),
                            '(\d)[.,\u00a0\u202f](\d)', '\1\2', 'g'),
                    '[^0-9]+', ' ', 'g'),
                '\m\d\M', '', 'g')
        )
    ) STORED;

COMMENT ON COLUMN email_body_text.digits_tsv IS
    'Digit-only tokens for numeric search (LUM-827). Separators BETWEEN digits '
    'are joined; everything else breaks, so digits from different numbers never '
    'merge. Queried with the :* prefix operator. Derived from the same '
    'COALESCE(text_stripped, text) as text_tsv — keep the two in step.';

CREATE INDEX IF NOT EXISTS email_body_text_digits_idx
    ON email_body_text USING GIN (digits_tsv);
