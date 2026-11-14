# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Signal infrastructure models.

Signal.raw_content is transient: populated by adapters for LLM processing,
cleared (set to "") after process_signal() runs. Never persisted to Postgres.
"""

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Optional


@dataclass
class Signal:
    signal_id: str
    source_id: str
    title: str
    url: str
    published_at: Optional[datetime]
    content_summary: str
    raw_content: str  # transient — LLM processing only, not persisted
    entities: list[dict]
    topics: list[str]
    importance_score: float
    relevance_score: float
    notified: bool
    created_at: datetime
    user_id: str = "default"
    # LUM-170: source_type lets process_signal identify email ("imap") signals
    # so the local-model privacy guard can fire on the real classification path.
    source_type: str = ""
    # LUM-170: category taxonomy label (email classification). Empty for
    # non-email signals. Persisted; see signal_processor._persist.
    category: str = ""
    # LUM-642: persisted sender for email signals — bare lowercased address
    # (from_addr) + display name (from_display) parsed from the From header.
    # Empty for non-email/historical signals. Rendered on the /email surface.
    from_addr: str = ""
    from_display: str = ""
    # RFC 5322 Message-ID for email signals; '' for every other source type.
    # PERSISTED (migration 069), unlike `headers` below — it is what lets an
    # attachment refetch survive the message being moved by any client or
    # external system, not only by Lumogis's own organize.
    email_message_id: str = ""
    # LUM-170: fetched header subset (From/Subject/Date/Message-ID/List-Unsubscribe)
    # for email signals — transient, feeds the classifier rules. Not persisted.
    headers: dict[str, str] = field(default_factory=dict)
    # LUM-695: attachment METADATA extracted by the IMAP adapter during the same
    # parse that produced raw_content. Transient like `headers` — not a column on
    # the signals row; signal_processor writes it to `email_attachment` (AFTER the
    # LUM-697 suppression return, so suppressed mail leaves no orphans). Typed
    # loosely to keep models/ free of a services/ import.
    attachments: list = field(default_factory=list)


@dataclass
class SourceConfig:
    id: str
    name: str
    source_type: str  # rss | page | playwright | caldav | paperless | imap
    url: str
    category: str
    active: bool
    poll_interval: int  # seconds
    # feedparser | trafilatura | playwright | caldav | paperless_http
    # | imap_metadata | imap_full_ingest
    extraction_method: str
    css_selector_override: Optional[str]
    last_polled_at: Optional[datetime]
    last_signal_at: Optional[datetime]
    # paperless ``added`` watermark (ISO); for imap: ``{uidvalidity}:{last_uid}``. NULL in DB only.
    poll_cursor: Optional[str] = None
    user_id: str = "default"


@dataclass
class RelevanceProfile:
    id: str
    tracked_locations: list[str] = field(default_factory=list)
    tracked_topics: list[str] = field(default_factory=list)
    tracked_entities: list[str] = field(default_factory=list)
    tracked_keywords: list[str] = field(default_factory=list)
    updated_at: Optional[datetime] = None
    user_id: str = "default"
    # LUM-649: feedback-derived weight deltas, additive on top of the explicit
    # tracked_* lists above. Empty by default so a profile from before this
    # chunk (or before its first feedback) is indistinguishable in behavior.
    learned_topic_weights: dict[str, float] = field(default_factory=dict)
    learned_entity_weights: dict[str, float] = field(default_factory=dict)
    learned_category_weights: dict[str, float] = field(default_factory=dict)
