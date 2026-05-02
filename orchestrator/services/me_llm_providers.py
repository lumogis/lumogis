# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Read-only ``GET /api/v1/me/llm-providers`` façade.

Surfaces **metadata only**: which cloud LLM connectors exist, whether rows
or env fallback may supply credentials, and the **storage tier** that would
be consulted first if decrypts succeed. Does **not** decrypt ciphertext,
does not read env values into the response, and does not change resolution
semantics elsewhere.

**Limitation:** If a per-user row exists but decrypt fails at runtime,
this view still reports ``active_tier="user"`` (row presence). Runtime
errors remain the domain of chat / ``resolve_runtime_credential``.
"""

from __future__ import annotations

import os
import re
from typing import Literal

from auth import auth_enabled
from connectors import registry as reg
from models.api_v1 import MeLlmProviderItem
from models.api_v1 import MeLlmProvidersResponse
from models.api_v1 import MeLlmProvidersSummary
from services.llm_connector_map import LLM_CONNECTOR_BY_ENV
from services.llm_connector_map import vendor_label_for_connector

from services import connector_credentials as ccs
from services import credential_tiers as ct

# Single source for LLM connector ids (kept in sync with ``models.yaml`` envs).
_CONNECTOR_IDS: tuple[str, ...] = tuple(sorted(frozenset(LLM_CONNECTOR_BY_ENV.values())))

_ENV_NAME_BY_CONNECTOR: dict[str, str] = {v: k for k, v in LLM_CONNECTOR_BY_ENV.items()}

# Strip registry prose that names payload keys / braces — wire copy stays generic.
_DESC_BRACES = re.compile(r"\{[^}]*\}")
_PAYLOAD_WORD = re.compile(r"\bpayload\b", re.IGNORECASE)
_TRAIL_JUNK = re.compile(r"\s*[,;—\-]+\s*$")


def llm_connector_ids() -> tuple[str, ...]:
    """Canonical ordered list of LLM connector ids (``llm_*``)."""
    return _CONNECTOR_IDS


def _safe_description(connector_id: str) -> str:
    spec = reg.CONNECTORS.get(connector_id)
    if spec is None:
        return ""
    text = _DESC_BRACES.sub("", spec.description)
    text = _PAYLOAD_WORD.sub("credential", text)
    text = " ".join(text.split())
    text = _TRAIL_JUNK.sub("", text).strip()
    return text[:2000]


def _env_fallback_configured(connector_id: str) -> bool:
    """True iff legacy single-user env fallback could apply (no secret values read)."""
    if auth_enabled():
        return False
    env_name = _ENV_NAME_BY_CONNECTOR.get(connector_id)
    if not env_name:
        return False
    return bool(os.environ.get(env_name, "").strip())


def build_me_llm_providers_response(user_id: str) -> MeLlmProvidersResponse:
    """Build the curated LLM provider list for ``user_id``."""
    providers: list[MeLlmProviderItem] = []
    for cid in _CONNECTOR_IDS:
        user_rec = ccs.get_record(user_id, cid)
        hh_rec = ct.household_get_record(cid)
        sys_rec = ct.system_get_record(cid)
        env_fb = _env_fallback_configured(cid)

        meta_rec: object | None
        if user_rec is not None:
            tier: Literal["user", "household", "system", "env", "none"] = "user"
            meta_rec = user_rec
        elif hh_rec is not None:
            tier = "household"
            meta_rec = hh_rec
        elif sys_rec is not None:
            tier = "system"
            meta_rec = sys_rec
        elif env_fb:
            tier = "env"
            meta_rec = None
        else:
            tier = "none"
            meta_rec = None

        configured = tier != "none"

        updated_at = getattr(meta_rec, "updated_at", None) if meta_rec is not None else None
        key_version = getattr(meta_rec, "key_version", None) if meta_rec is not None else None

        if configured:
            status: Literal["configured", "not_configured"] = "configured"
            why_not = None
        else:
            status = "not_configured"
            why_not = (
                "No credential stored at user, household, or system tier, and no env fallback."
            )

        providers.append(
            MeLlmProviderItem(
                connector=cid,
                label=vendor_label_for_connector(cid),
                description=_safe_description(cid),
                configured=configured,
                active_tier=tier,
                user_credential_present=user_rec is not None,
                household_credential_available=hh_rec is not None,
                system_credential_available=sys_rec is not None,
                env_fallback_available=env_fb,
                updated_at=updated_at,
                key_version=key_version,
                status=status,
                why_not_available=why_not,
            )
        )

    total = len(providers)
    n_cfg = sum(1 for p in providers if p.configured)
    by_tier: dict[str, int] = {}
    for p in providers:
        by_tier[p.active_tier] = by_tier.get(p.active_tier, 0) + 1
    by_tier_sorted = dict(sorted(by_tier.items()))

    return MeLlmProvidersResponse(
        providers=providers,
        summary=MeLlmProvidersSummary(
            total=total,
            configured=n_cfg,
            not_configured=total - n_cfg,
            by_active_tier=by_tier_sorted,
        ),
    )
