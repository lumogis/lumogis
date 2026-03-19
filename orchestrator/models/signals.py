# SPDX-License-Identifier: AGPL-3.0-or-later
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


@dataclass
class SourceConfig:
    id: str
    name: str
    source_type: str  # rss | page | playwright | caldav
    url: str
    category: str
    active: bool
    poll_interval: int  # seconds
    extraction_method: str  # feedparser | trafilatura | playwright | caldav
    css_selector_override: Optional[str]
    last_polled_at: Optional[datetime]
    last_signal_at: Optional[datetime]
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
