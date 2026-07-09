# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Cloud LLM privacy mode policy resolution and enforcement (LUM-194)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from actions.audit import write_audit
from models.actions import AuditEntry
from models.privacy_mode import InstancePrivacyMode
from models.privacy_mode import PrivacyUserRestriction
from settings_store import get_setting

import config

_log = logging.getLogger(__name__)

PRIVACY_FALLBACK_MESSAGE = (
    "Privacy mode is local-only. This reply used your local model ({model}); "
    "complex synthesis may be slower or lower quality."
)


class PrivacyModeBlocked(Exception):
    """Raised when a remote model is requested under local-only policy with no fallback."""

    def __init__(
        self,
        model_name: str,
        *,
        effective_policy: str = InstancePrivacyMode.LOCAL_ONLY.value,
    ) -> None:
        self.model_name = model_name
        self.effective_policy = effective_policy
        super().__init__(
            f"Privacy mode is local-only. Cloud model '{model_name}' is not permitted."
        )


@dataclass(frozen=True)
class EffectivePrivacy:
    instance_mode: InstancePrivacyMode
    instance_locked: bool
    instance_effective: InstancePrivacyMode
    user_restriction: PrivacyUserRestriction
    effective: InstancePrivacyMode


def _truthy_setting(raw: str | None) -> bool:
    return bool(raw and raw.strip().lower() in ("true", "1", "yes"))


def _load_instance_mode(store) -> InstancePrivacyMode:
    raw = get_setting("privacy_mode", store)
    if raw is None or not str(raw).strip():
        return InstancePrivacyMode.LOCAL_ONLY
    try:
        return InstancePrivacyMode(str(raw).strip())
    except ValueError:
        return InstancePrivacyMode.LOCAL_ONLY


def _load_instance_locked(store) -> bool:
    return _truthy_setting(get_setting("privacy_mode_locked", store))


def _load_user_restriction(user_id: str) -> PrivacyUserRestriction:
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: per-user privacy preference row (not scope-gated household data).
    row = ms.fetch_one(
        "SELECT restriction FROM privacy_user_settings WHERE user_id = %s",
        (user_id,),
    )
    if not row:
        return PrivacyUserRestriction.INHERIT
    raw = row.get("restriction") or PrivacyUserRestriction.INHERIT.value
    try:
        return PrivacyUserRestriction(str(raw))
    except ValueError:
        return PrivacyUserRestriction.INHERIT


def effective_privacy_mode(user_id: str | None) -> EffectivePrivacy:
    """Resolve instance + per-user effective privacy policy."""
    from auth import auth_enabled

    store = config.get_metadata_store()
    instance_mode = _load_instance_mode(store)
    locked = _load_instance_locked(store)

    if instance_mode == InstancePrivacyMode.LOCAL_ONLY:
        instance_effective = InstancePrivacyMode.LOCAL_ONLY
    else:
        instance_effective = InstancePrivacyMode.ALLOW_CLOUD

    if auth_enabled() and user_id is not None:
        user_restriction = _load_user_restriction(user_id)
        if user_restriction == PrivacyUserRestriction.LOCAL_ONLY:
            effective = InstancePrivacyMode.LOCAL_ONLY
        else:
            effective = instance_effective
    else:
        user_restriction = PrivacyUserRestriction.INHERIT
        effective = instance_effective

    return EffectivePrivacy(
        instance_mode=instance_mode,
        instance_locked=locked,
        instance_effective=instance_effective,
        user_restriction=user_restriction,
        effective=effective,
    )


def is_remote_model(model_name: str) -> bool:
    """Fail-closed: remote = not positively local."""
    return not config.is_local_model(model_name)


def blocks_remote_models(user_id: str | None) -> bool:
    return effective_privacy_mode(user_id).effective == InstancePrivacyMode.LOCAL_ONLY


def record_privacy_block(
    *,
    requested_model: str,
    user_id: str | None,
    policy: EffectivePrivacy | None = None,
) -> None:
    pol = policy or effective_privacy_mode(user_id)
    summary = {
        "decline_type": "external_call_denied",
        "reason": "privacy_mode_block",
        "requested_model": requested_model,
        "effective_policy": pol.effective.value,
        "instance_mode": pol.instance_mode.value,
        "instance_locked": pol.instance_locked,
        "user_restriction": pol.user_restriction.value,
    }
    write_audit(
        AuditEntry(
            action_name="privacy_mode_block",
            connector="llm",
            mode="privacy_gate",
            user_id=user_id or "default",
            input_summary=json.dumps(summary),
            result_summary="",
            reverse_action=None,
        )
    )


def assert_remote_allowed(model_name: str, user_id: str | None) -> None:
    """Raise PrivacyModeBlocked before remote adapter construction."""
    if not is_remote_model(model_name):
        return
    pol = effective_privacy_mode(user_id)
    if pol.effective == InstancePrivacyMode.ALLOW_CLOUD:
        return
    record_privacy_block(requested_model=model_name, user_id=user_id, policy=pol)
    raise PrivacyModeBlocked(model_name, effective_policy=pol.effective.value)


def resolve_local_fallback_model(user_id: str | None) -> str | None:
    """Pick a local model for chat/job fallback under local-only policy."""
    store = config.get_metadata_store()
    blocks = blocks_remote_models(user_id)

    default_raw = get_setting("default_model", store)
    if default_raw and config.is_local_model(default_raw):
        if config.is_model_enabled(
            default_raw,
            user_id=user_id,
            _privacy_blocks_remote=blocks,
        ):
            return default_raw

    for name in config.get_all_models_config():
        if not config.is_local_model(name):
            continue
        if config.is_model_enabled(
            name,
            user_id=user_id,
            _privacy_blocks_remote=blocks,
        ):
            return name

    if config.is_local_model("llama"):
        return "llama"
    return None


def build_privacy_metadata(*, requested_model: str, effective_model: str) -> dict:
    return {
        "fallback_applied": True,
        "requested_model": requested_model,
        "message": PRIVACY_FALLBACK_MESSAGE.format(model=effective_model),
    }


def resolve_model_for_request(
    requested_model: str,
    user_id: str | None,
) -> tuple[str, dict | None]:
    """Return (effective_model, optional lumogis.privacy metadata for chat fallback)."""
    if not is_remote_model(requested_model):
        return requested_model, None

    pol = effective_privacy_mode(user_id)
    if pol.effective == InstancePrivacyMode.ALLOW_CLOUD:
        return requested_model, None

    fallback = resolve_local_fallback_model(user_id)
    if fallback is None:
        record_privacy_block(requested_model=requested_model, user_id=user_id, policy=pol)
        raise PrivacyModeBlocked(requested_model, effective_policy=pol.effective.value)

    _log.info(
        "privacy_mode: fallback %s -> %s (user=%s)",
        requested_model,
        fallback,
        user_id,
    )
    return fallback, build_privacy_metadata(
        requested_model=requested_model,
        effective_model=fallback,
    )


def resolve_job_model(requested_model: str, user_id: str | None) -> str | None:
    """Background jobs: swap to local fallback or skip when unavailable."""
    try:
        effective, meta = resolve_model_for_request(requested_model, user_id)
        if meta:
            _log.info(
                "privacy_mode: background fallback %s -> %s (user=%s)",
                requested_model,
                effective,
                user_id,
            )
        return effective
    except PrivacyModeBlocked:
        _log.warning(
            "privacy_mode: no local fallback for background model=%s user=%s; skipping LLM",
            requested_model,
            user_id,
        )
        return None


def get_instance_privacy_settings() -> dict:
    pol = effective_privacy_mode(None)
    return {
        "privacy_mode": pol.instance_mode.value,
        "privacy_mode_locked": pol.instance_locked,
        "privacy_effective": pol.instance_effective.value,
    }


def get_me_privacy_mode(user_id: str) -> dict:
    pol = effective_privacy_mode(user_id)
    can_allow = (
        pol.instance_effective == InstancePrivacyMode.ALLOW_CLOUD
        and not pol.instance_locked
        and pol.user_restriction != PrivacyUserRestriction.LOCAL_ONLY
    )
    return {
        "instance": {
            "privacy_mode": pol.instance_mode.value,
            "privacy_mode_locked": pol.instance_locked,
            "privacy_effective": pol.instance_effective.value,
        },
        "user_restriction": pol.user_restriction.value,
        "privacy_effective": pol.effective.value,
        "can_allow_cloud": can_allow,
    }


def patch_me_privacy_mode(user_id: str, restriction: PrivacyUserRestriction) -> dict:
    from fastapi import HTTPException

    pol = effective_privacy_mode(user_id)

    if restriction == PrivacyUserRestriction.INHERIT:
        if (
            pol.instance_effective == InstancePrivacyMode.LOCAL_ONLY
            and pol.user_restriction == PrivacyUserRestriction.LOCAL_ONLY
        ):
            raise HTTPException(status_code=403, detail="privacy_restriction_denied")
        ms = config.get_metadata_store()
        # SCOPE-EXEMPT: delete caller-owned privacy preference row only.
        ms.execute("DELETE FROM privacy_user_settings WHERE user_id = %s", (user_id,))
        return get_me_privacy_mode(user_id)

    if restriction == PrivacyUserRestriction.LOCAL_ONLY:
        if pol.instance_effective == InstancePrivacyMode.LOCAL_ONLY:
            # Already local-only at instance; storing row is idempotent but allowed
            pass
        ms = config.get_metadata_store()
        ms.execute(
            """
            INSERT INTO privacy_user_settings (user_id, restriction, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE
              SET restriction = EXCLUDED.restriction,
                  updated_at = NOW()
            """,
            (user_id, restriction.value),
        )
        return get_me_privacy_mode(user_id)

    raise ValueError("invalid restriction")


def validate_instance_privacy_patch(
    *,
    current_mode: InstancePrivacyMode,
    current_locked: bool,
    new_mode: InstancePrivacyMode | None,
    new_locked: bool | None,
) -> None:
    from fastapi import HTTPException

    mode = new_mode if new_mode is not None else current_mode
    locked = new_locked if new_locked is not None else current_locked

    if locked and mode == InstancePrivacyMode.ALLOW_CLOUD:
        raise HTTPException(status_code=400, detail="privacy_lock_requires_local_only")

    if (
        current_locked
        and current_mode == InstancePrivacyMode.LOCAL_ONLY
        and new_mode == InstancePrivacyMode.ALLOW_CLOUD
        and not (new_locked is False)
    ):
        raise HTTPException(status_code=400, detail="privacy_mode_locked")
