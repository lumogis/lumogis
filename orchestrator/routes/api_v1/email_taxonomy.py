# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/me/email-taxonomy`` — user-editable email category taxonomy +
classification rules (LUM-629).

Web-facing façade so the Lumogis-Web Labels page can reach these settings
(the vite/nginx proxy forwards ``/api`` — not root ``/me`` — to the
orchestrator). All routes are per-user (``require_user``); the store lives in
``services/email_taxonomy.py``.
"""

from __future__ import annotations

from auth import UserContext
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from models.email_taxonomy import ReclassifyResponse
from models.email_taxonomy import RulesPatch
from models.email_taxonomy import RulesResponse
from models.email_taxonomy import TaxonomyPatch
from models.email_taxonomy import TaxonomyResponse

from services import email_taxonomy

router = APIRouter(prefix="/api/v1/me", tags=["email-taxonomy"])


@router.get("/email-taxonomy", response_model=TaxonomyResponse)
def get_email_taxonomy(user: UserContext = Depends(require_user)):
    return TaxonomyResponse(categories=email_taxonomy.load_taxonomy(user.user_id))


@router.put("/email-taxonomy", response_model=TaxonomyResponse)
def put_email_taxonomy(body: TaxonomyPatch, user: UserContext = Depends(require_user)):
    return TaxonomyResponse(categories=email_taxonomy.put_taxonomy(user.user_id, body))


@router.get("/email-classification-rules", response_model=RulesResponse)
def get_email_rules(user: UserContext = Depends(require_user)):
    return RulesResponse(rules=email_taxonomy.list_rules(user.user_id))


@router.put("/email-classification-rules", response_model=RulesResponse)
def put_email_rules(body: RulesPatch, user: UserContext = Depends(require_user)):
    return RulesResponse(rules=email_taxonomy.put_rules(user.user_id, body.rules))


@router.post("/email-taxonomy/{slug}/reclassify", response_model=ReclassifyResponse)
def reclassify_email_category(slug: str, user: UserContext = Depends(require_user)):
    return ReclassifyResponse(**email_taxonomy.reclassify(user.user_id, slug))
