# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Local search over ingested mail — subject, sender and message text (LUM-819).

Two engines, deliberately, because there are two corpora:

* **Subject / sender / summary** are short, identifier-like and typo-prone, so
  they are substring-matched with ``ILIKE`` over ``pg_trgm`` GIN indexes.
* **Message text** is prose, so it is word-matched against a ``tsvector`` GIN,
  which is a far smaller index and brings ranking with it.

The two behave differently, and the user cannot see which one answered — which
is why every response carries **per-field match counts**. Search fails silently
by nature: an empty list is indistinguishable from an absent message. The counts
are what make the difference legible instead of mysterious.

**Scope is split, deliberately.** Subject and sender use the household union
(:func:`visibility.visible_filter`), matching what the triage lanes already
show. Message text uses :func:`visibility.authored_by_filter` — **owner only** —
because LUM-810 decided shared signals get no read affordance, and a body match
on a household-shared signal would leak through search exactly what the read
view refuses.

**The sanitiser in :func:`to_prefix_tsquery` is a security control.** Prefix
matching cannot be expressed through ``websearch_to_tsquery``, so this module
builds its own ``tsquery`` and takes back the injection risk that function
existed to remove. Every ``tsquery`` metacharacter is a non-word character, so
stripping non-word characters removes all of them. Weaken that strip and a 500
becomes reachable from the search box. Note the class is Unicode-aware on
purpose: an ASCII-only strip turns ``Müller`` into ``Mller`` and silently breaks
searching for a German correspondent by name.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from visibility import UserContext
from visibility import authored_by_filter
from visibility import visible_filter

import config
from services import email_digits
from services import signal_triage
from services import sql_like

_log = logging.getLogger(__name__)

#: Trigram indexes degrade badly below three characters, and a two-character
#: prefix query matches most of the corpus.
MIN_QUERY_CHARS = 3

#: Counting every matching row cannot run under the result ``LIMIT``, so the
#: count is bounded and the UI renders "200+" beyond it.
MAX_COUNT = 200

#: Every tsquery metacharacter (``& | ! ( ) : * ' \ < >``) is a NON-WORD
#: character, so this removes all of them. ``re.UNICODE`` is load-bearing:
#: ``[^A-Za-z0-9]`` would also strip umlauts and break German search.
_TSQUERY_STRIP = re.compile(r"[^\w]", re.UNICODE)

Fields = Literal["all", "subject", "sender", "body"]
VALID_FIELDS: frozenset[str] = frozenset({"all", "subject", "sender", "body"})


class QueryTooShort(ValueError):
    """The query is unusable — too short, or nothing searchable survived."""


@dataclass(frozen=True)
class NormalisedQuery:
    """One user query in the forms the engines need."""

    raw: str
    tsquery: str  # for to_tsquery('english', …) — prefixed, sanitised
    like: str  # for ILIKE … ESCAPE '\' — wildcard-escaped
    digits: str = ""  # bare digits for the numeric index, or "" — see below

    @property
    def digits_tsquery(self) -> str:
        """``2400:*`` — prefix, matching migration 078. ``""`` when not numeric.

        Built here rather than at each call site so the ``:*`` cannot be
        forgotten by one of them: without it ``2400`` would have to equal the
        whole token and would stop finding ``2,400.00`` (stored as ``240000``),
        which is the entire feature.
        """
        return f"{self.digits}:*" if self.digits else ""


def to_prefix_tsquery(q: str) -> str:
    """Build a safe prefixed ``tsquery``. Returns ``''`` when nothing remains.

    Each whitespace-separated token is stripped of non-word characters and given
    the ``:*`` prefix operator, then the tokens are ``AND``-ed. Prefix matching
    is what makes the ``'english'`` index safe for non-English mail — measured,
    ``Rechnung:*`` matches "rechnungen", which plain ``'english'`` misses.
    """
    toks = [_TSQUERY_STRIP.sub("", t) for t in q.split()]
    return " & ".join(f"{t}:*" for t in toks if t)


def to_like_pattern(q: str) -> str:
    r"""Wrap ``q`` as ``%…%``, escaping LIKE wildcards for ``ESCAPE '\'``.

    Backslash is escaped FIRST; doing ``%`` or ``_`` first would then double
    escape the backslashes it introduced and corrupt the pattern. Without this
    a user searching ``100%`` matches every row and gets a sequential scan.
    """
    return f"%{sql_like.escape_like(q)}%"


def normalise_query(q: str) -> NormalisedQuery:
    """Validate and normalise, or raise :class:`QueryTooShort`.

    Rejects a punctuation-only query explicitly. ``to_tsquery('english','')``
    does not raise — it matches nothing and emits a NOTICE — so without this
    guard the user gets a silent empty list rather than being told why.
    """
    stripped = (q or "").strip()
    if len(stripped) < MIN_QUERY_CHARS:
        raise QueryTooShort(f"at least {MIN_QUERY_CHARS} characters")
    tsquery = to_prefix_tsquery(stripped)
    if not tsquery:
        raise QueryTooShort(f"at least {MIN_QUERY_CHARS} letters or numbers")
    return NormalisedQuery(
        raw=stripped,
        tsquery=tsquery,
        like=to_like_pattern(stripped),
        # LUM-827 — non-empty only for a BARE numeric query. `email_digits`
        # returns "" the moment a letter appears, so `INV-2400` never reaches
        # the numeric index; see its docstring for why that limit is deliberate.
        digits=email_digits.normalise_query(stripped),
    )


# --------------------------------------------------------------------------- #
# Predicate construction
# --------------------------------------------------------------------------- #

_SUBJECT_ARM = "title ILIKE %s ESCAPE '\\'"
_SUMMARY_ARM = "content_summary ILIKE %s ESCAPE '\\'"
_SENDER_ARM = "from_addr ILIKE %s ESCAPE '\\' OR from_display ILIKE %s ESCAPE '\\'"
_BODY_TEXT_ARM = "b.text_tsv @@ to_tsquery('english', %s)"
#: LUM-827 — the numeric index (migration 078). ``simple``, not ``english``:
#: these are digit runs, so stemming is meaningless and stop words could only
#: remove tokens.
_BODY_DIGITS_ARM = "b.digits_tsv @@ to_tsquery('simple', %s)"


def body_arm(nq: NormalisedQuery) -> tuple[str, tuple]:
    """``(sql, params)`` for "the message text matched" — word index, number index.

    Emitted from ONE place because four sites need it in step: the match
    predicate, the ranking CASE, the ``body_matched`` select and the counts
    CTE. If any of them omitted the numeric arm the query would disagree with
    itself — a message returned but reported as not matching the body, or
    counted in the chip and absent from the results.

    The numeric arm is emitted **only for a bare numeric query**, so an
    ordinary word search keeps exactly the SQL and the parameter count it had
    before LUM-827, and never pays for an index it cannot use.
    """
    if nq.digits:
        return f"({_BODY_TEXT_ARM} OR {_BODY_DIGITS_ARM})", (nq.tsquery, nq.digits_tsquery)
    return _BODY_TEXT_ARM, (nq.tsquery,)


def build_match_predicate(nq: NormalisedQuery, *, fields: str = "all") -> tuple[str, tuple]:
    """Return ``(parenthesised OR-joined clause, params)`` for the chosen fields.

    Only the selected arms are emitted. ``content_summary`` rides with
    ``subject`` because the summary is Lumogis's paraphrase OF the subject, not
    an independent field a user would think to select.
    """
    arms: list[str] = []
    params: list = []
    if fields in ("all", "subject"):
        arms.append(_SUBJECT_ARM)
        params.append(nq.like)
    if fields in ("all", "sender"):
        arms.append(_SENDER_ARM)
        params.extend([nq.like, nq.like])
    if fields in ("all", "subject"):
        arms.append(_SUMMARY_ARM)
        params.append(nq.like)
    if fields in ("all", "body"):
        arm_sql, arm_params = body_arm(nq)
        arms.append(arm_sql)
        params.extend(arm_params)
    if not arms:  # pragma: no cover - guarded by route validation
        raise ValueError(f"unknown fields selector: {fields!r}")
    return "(" + " OR ".join(arms) + ")", tuple(params)


def _order_by(nq: NormalisedQuery, fields: str) -> str:
    """Ranking. Single-field searches have nothing to blend, so they don't.

    The ``content_summary`` arm is scored deliberately: without it a
    summary-only hit scores zero and ranks BELOW every body hit, contradicting
    the precedence the surface reports. ``created_at DESC`` is the final
    tie-break so the ordering is total and pagination is deterministic.

    ``ts_rank_cd`` stays on ``text_tsv`` alone. A numeric-only hit therefore
    scores zero there and falls through to ``created_at DESC``, which is the
    honest ordering: every message quoting the same reference number is equally
    relevant to "2400", and inventing a tie-break between them would only make
    an arbitrary order look considered. Whether it matched at all is scored, so
    a numeric hit still outranks a non-matching row.
    """
    body_sql, _ = body_arm(nq)
    if fields == "body":
        return "ts_rank_cd(b.text_tsv, to_tsquery('english', %s)) DESC NULLS LAST, created_at DESC"
    if fields in ("subject", "sender"):
        return "created_at DESC"
    return (
        "(CASE WHEN title ILIKE %s ESCAPE '\\' THEN 4 ELSE 0 END"
        " + CASE WHEN from_addr ILIKE %s ESCAPE '\\'"
        " OR from_display ILIKE %s ESCAPE '\\' THEN 3 ELSE 0 END"
        " + CASE WHEN content_summary ILIKE %s ESCAPE '\\' THEN 2 ELSE 0 END"
        f" + CASE WHEN {body_sql} THEN 1 ELSE 0 END) DESC,"
        " ts_rank_cd(b.text_tsv, to_tsquery('english', %s)) DESC NULLS LAST,"
        " created_at DESC"
    )


def _order_by_params(nq: NormalisedQuery, fields: str) -> tuple:
    if fields == "body":
        return (nq.tsquery,)
    if fields in ("subject", "sender"):
        return ()
    _, body_params = body_arm(nq)
    return (nq.like, nq.like, nq.like, nq.like) + body_params + (nq.tsquery,)


def _matched_in(row: dict, nq: NormalisedQuery) -> list[str]:
    """Which arms this row satisfied, in ranking precedence order.

    Recomputed in Python rather than selected as four extra SQL expressions:
    the row is already in hand, and the ILIKE arms are a plain case-insensitive
    substring test. The body arm is authoritative from SQL — token matching is
    not reproducible with ``in``.
    """
    needle = nq.raw.lower()
    out: list[str] = []
    if needle in (row.get("title") or "").lower():
        out.append("subject")
    if (
        needle in (row.get("from_addr") or "").lower()
        or needle in (row.get("from_display") or "").lower()
    ):
        out.append("sender")
    if needle in (row.get("content_summary") or "").lower():
        out.append("summary")
    if row.get("body_matched"):
        out.append("body")
    return out


def _body_lateral(user_id: str) -> tuple[str, tuple]:
    """The owner-only LATERAL that exposes stored message text.

    Scoped INSIDE the join, not in the outer WHERE, so ``b.*`` is NULL for any
    row the caller did not author. Everything downstream — the match arm, the
    ranking, the snippet — is then owner-safe by construction rather than by
    each site remembering to re-check.

    A plain ``JOIN`` here would raise **42702 ambiguous column**: both
    ``signals`` and ``email_body_text`` carry ``user_id`` and ``signal_id``,
    while ``_SIGNALS_COLUMNS`` and ``visible_filter`` use unqualified columns.
    That is the same trap the triage LATERAL exists to avoid, and it is a
    runtime error no fake store reproduces — hence the qualified filter.
    """
    owner_sql, owner_params = authored_by_filter(user_id, qualifier="signals")
    return (
        "LEFT JOIN LATERAL ("
        # LUM-826 — the snippet must window over the SAME text the tsvector was
        # built from, or a hit on new content can be excerpted from the quoted
        # copy further down the message. COALESCE mirrors the generated column
        # in migration 075 exactly; if the two ever drift, snippets start
        # pointing at text that did not match.
        "SELECT COALESCE(ebt.text_stripped, ebt.text) AS text, "
        # LUM-827 — `digits_tsv` is exposed here for the same reason `text_tsv`
        # is: the body arm reads it through `b`, and a column left out of this
        # list is a runtime 42703 that no fake store reproduces.
        "ebt.truncated, ebt.text_tsv, ebt.digits_tsv "
        "FROM email_body_text ebt "
        f"WHERE ebt.signal_id = signals.signal_id AND {owner_sql}"
        ") b ON TRUE",
        owner_params,
    )


#: Email signals carry an ``imap://{source_id}/{uidvalidity}/{uid}`` url. Without
#: this an RSS or page signal whose title matched would be returned by *email*
#: search. Same predicate `routes/signals.py` uses to scope its email view.
#:
#: The ``%%`` is NOT a typo. psycopg2 %-formats the whole query string before it
#: reaches Postgres, so a lone ``%`` in a statement executed WITH a params tuple
#: raises before the server sees it. `routes/signals.py` dodges this by binding
#: "imap://%" as a VALUE; here it is a literal, so it must be doubled.
_EMAIL_ONLY = "url LIKE 'imap://%%'"


def search_email(
    user: UserContext,
    q: str,
    *,
    fields: str = "all",
    source_id: str | None = None,
    limit: int = 50,
) -> dict:
    """Search the caller's mail. The only public entry point.

    Raises :class:`QueryTooShort` for an unusable query. The caller's identity
    comes from ``user`` and never from a parameter.
    """
    if fields not in VALID_FIELDS:
        raise ValueError(f"unknown fields selector: {fields!r}")
    nq = normalise_query(q)
    ms = config.get_metadata_store()

    join_sql, join_params = _body_lateral(user.user_id)
    where_sql, where_params = _compose_where(user, nq, fields=fields, source_id=source_id)

    body_sql, body_params = body_arm(nq)
    sql = signal_triage.build_signals_query(
        where_sql,
        _order_by(nq, fields),
        extra_join=join_sql,
        extra_select=(
            f"b.text AS body_text, b.truncated AS body_truncated, ({body_sql}) AS body_matched"
        ),
    )

    # Placeholder order is the whole ballgame, and it follows TEXTUAL order in
    # the assembled SQL — not the order the clauses were reasoned about:
    #
    #   SELECT … <extra_select %s>   ← 1. the body-match tsquery, PLUS the
    #                                     numeric one for a bare numeric query
    #                                     (LUM-827) — `body_arm` decides, and
    #                                     the params come from the same call
    #                                     that built the SQL, so the two cannot
    #                                     disagree about how many there are
    #   FROM signals <triage %s>     ← 2. the caller's id, for the triage LATERAL
    #                <body %s>       ← 3. the caller's id, for the body LATERAL
    #   WHERE …                      ← 4. visibility, mailbox, match arms
    #   ORDER BY …                   ← 5. ranking
    #   LIMIT %s                     ← 6.
    #
    # Binding either user_id late authorises the query as a DIFFERENT user —
    # a scope bug that no test of the predicate TEXT would catch. Pinned by
    # test_placeholder_order_binds_the_caller_before_the_predicates.
    params: list = [*body_params, user.user_id]
    params.extend(join_params)
    params.extend(where_params)
    params.extend(_order_by_params(nq, fields))
    params.append(limit)

    try:
        rows = ms.fetch_all(sql, tuple(params)) or []
    except Exception:
        _log.exception("email_search_failed")  # never logs the query or any mail
        raise

    hits = []
    for row in rows:
        hit = dict(row)
        body_text = hit.pop("body_text", None)
        hit["body_truncated"] = bool(hit.pop("body_truncated", False))
        matched = _matched_in(hit, nq)
        hit.pop("body_matched", None)
        hit["matched_in"] = matched
        hit["snippet"] = _snippet(body_text, nq) if "body" in matched else ""
        hits.append(hit)

    return {
        "hits": hits,
        "counts": _counts(user, nq, source_id=source_id),
        "fields": fields,
        "truncated": len(hits) >= limit,
        "searched_from": _searched_from(user, source_id=source_id),
    }


def _compose_where(
    user: UserContext,
    nq: NormalisedQuery,
    *,
    fields: str,
    source_id: str | None,
) -> tuple[str, tuple]:
    """``(clause, params)`` — email-only, visible, optionally one mailbox, matching."""
    clauses = [_EMAIL_ONLY]
    params: list = []

    visible_sql, visible_params = visible_filter(user)
    clauses.append(visible_sql)
    params.extend(visible_params)

    if source_id:
        clauses.append("source_id = %s")
        params.append(source_id)

    match_sql, match_params = build_match_predicate(nq, fields=fields)
    clauses.append(match_sql)
    params.extend(match_params)

    return " AND ".join(clauses), tuple(params)


def _counts(user: UserContext, nq: NormalisedQuery, *, source_id: str | None) -> dict:
    """Per-field match counts — what makes the two engines' difference legible.

    Bounded: an exact count cannot run under the result ``LIMIT``, so it is
    capped and the surface renders ``200+``. Presenting an unbounded scan as
    free would be the wrong trade on a household box.
    """
    ms = config.get_metadata_store()
    join_sql, join_params = _body_lateral(user.user_id)
    where_sql, where_params = _compose_where(user, nq, fields="all", source_id=source_id)

    # Bounded CTE, then aggregate: the LIMIT is what keeps this from scanning
    # every match. Placeholders bind in TEXTUAL order — the five SELECT
    # expressions first, then the LATERAL's user_id, then the WHERE predicates.
    body_sql, body_params = body_arm(nq)
    sql = (
        "WITH m AS ("
        " SELECT (title ILIKE %s ESCAPE '\\'"
        " OR content_summary ILIKE %s ESCAPE '\\') AS is_subject,"
        " (from_addr ILIKE %s ESCAPE '\\'"
        " OR from_display ILIKE %s ESCAPE '\\') AS is_sender,"
        f" ({body_sql}) AS is_body"
        f" FROM signals {join_sql}"
        f" WHERE {where_sql}"
        # ORDER BY is required, not cosmetic: LIMIT without it samples an
        # ARBITRARY 200 rows, so above the bound the per-field chips would
        # describe a random window rather than the newest matches. The chips
        # are the feature's whole point, so a wrong "Message 0" is worse than
        # a capped one. Same ordering as the default result page, so the counts
        # describe the rows the user is actually looking at.
        " ORDER BY created_at DESC"
        f" LIMIT {MAX_COUNT}"
        ") SELECT count(*) AS all_n,"
        " count(*) FILTER (WHERE is_subject) AS subject_n,"
        " count(*) FILTER (WHERE is_sender) AS sender_n,"
        " count(*) FILTER (WHERE is_body) AS body_n"
        " FROM m"
    )
    params = (
        [nq.like, nq.like, nq.like, nq.like]
        + list(body_params)
        + list(join_params)
        + list(where_params)
    )
    try:
        row = ms.fetch_one(sql, tuple(params)) or {}
    except Exception:
        _log.exception("email_search_counts_failed")
        return {"all": 0, "subject": 0, "sender": 0, "body": 0, "capped": False}

    counts = {
        "all": int(row.get("all_n") or 0),
        "subject": int(row.get("subject_n") or 0),
        "sender": int(row.get("sender_n") or 0),
        "body": int(row.get("body_n") or 0),
    }
    counts["capped"] = counts["all"] >= MAX_COUNT
    return counts


def _searched_from(user: UserContext, *, source_id: str | None):
    """Oldest mail Lumogis HOLDS — not the mailbox's oldest message.

    Feeds the empty state, where saying "we have mail back to March" is the
    honest answer to "why can't I find something from last year" (LUM-825).
    """
    ms = config.get_metadata_store()
    visible_sql, visible_params = visible_filter(user)
    clauses = [_EMAIL_ONLY, visible_sql]
    params = list(visible_params)
    if source_id:
        clauses.append("source_id = %s")
        params.append(source_id)
    try:
        row = ms.fetch_one(
            f"SELECT MIN(created_at) AS oldest FROM signals WHERE {' AND '.join(clauses)}",
            tuple(params),
        )
    except Exception:
        _log.exception("email_search_searched_from_failed")
        return None
    return (row or {}).get("oldest")


def _snippet(text: str | None, nq: NormalisedQuery, *, width: int = 300) -> str:
    """A window around the first occurrence of any query token in ``text``.

    Python-side rather than ``ts_headline``, which re-parses the document per
    row and is the most expensive thing that could be added to this query. The
    trade is honest: ``tsvector`` matched a normalised token while this searches
    raw text, so when no window can be found the snippet is **empty rather than
    wrong**.
    """
    if not text:
        return ""
    low = text.lower()
    at = -1
    if nq.digits:
        # LUM-827 — for a numeric query the digits index is what matched, so its
        # notion of where the number is, is the authoritative one. This runs
        # BEFORE the substring pass, not after it, and the ordering is the whole
        # point: a plain `find("2400")` happily lands INSIDE `124005`, which the
        # digits index deliberately did not match (prefix, not substring). The
        # snippet would then excerpt around a reference number the user did not
        # search for while omitting the amount they did — a wrong excerpt on a
        # right result, which is worse than no excerpt at all.
        #
        # It also covers the case that motivated the function: the index holds
        # `240000` while the text says `2,400.00`, so the substring pass fails
        # outright on exactly the hits this feature exists to produce.
        at = email_digits.find_digit_span(text, nq.digits)
    if at < 0:
        for tok in nq.raw.lower().split():
            at = low.find(tok)
            if at >= 0:
                break
    if at < 0:
        return ""
    start = max(0, at - width // 3)
    end = min(len(text), start + width)
    out = text[start:end]
    if start:
        out = out[out.find(" ") + 1 :] if " " in out else out
        out = "…" + out
    if end < len(text):
        out = (out[: out.rfind(" ")] if " " in out else out) + "…"
    return out
