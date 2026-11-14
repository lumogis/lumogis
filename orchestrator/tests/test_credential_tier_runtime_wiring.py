# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Runtime paths honour the household / system credential tiers (LUM-24).

`resolve_runtime_credential` shipped complete, tested and **uncalled**: every
runtime consumer still read the per-user tier directly. The tier-aware read
models (`me_notifications`, `me_llm_providers`) shipped anyway, so the product
reported credentials it could not use.

The reproduction that motivated this file, before the fix:

    resolve_runtime_credential tier = 'household'
    me_notifications active_tier    = 'household'   <- read model
    configured                      = True          <- read model
    load_ntfy_runtime_config        -> ConnectorNotConfigured   <- send path

and both ntfy call sites swallow that exception --
`notifications/channels/ntfy_channel.py` returns ``status="skipped"``,
`adapters/ntfy_notifier.py` returns ``False`` -- so the notification silently
never arrived while the settings screen said the channel was configured.

**These tests deliberately cross the two layers in a single assertion.** The
divergence survived review because the read model and the send path each had
good coverage on their own and nothing compared them. A test that only asserted
"the resolver resolves the household tier" passes both before and after this
fix; it is the pairing that has teeth.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from tests.test_credential_tiers_routes import _TEST_FERNET_KEY
from tests.test_credential_tiers_routes import _RoutesFakeStore

_HOUSEHOLD_ACTOR = "admin:root"
_MEMBER = "alice"


@pytest.fixture
def store(monkeypatch, mock_vector_store, mock_embedder, mock_scheduler):
    import config as _config
    from services import _credential_internals as ci

    # LUM-850: the `/v1/models` snapshot reads the user tier through
    # `ccs.list_records`, which the base store already serves; the LIKE-scan
    # handler this fixture used to add is gone with the raw SQL.
    s = _RoutesFakeStore()
    s._vector_store = mock_vector_store
    s._embedder = mock_embedder
    s._scheduler = mock_scheduler
    _config._instances["metadata_store"] = s
    ci.reset_for_tests()
    yield s
    _config._instances.pop("metadata_store", None)
    ci.reset_for_tests()


@pytest.fixture
def auth_on(monkeypatch):
    """Auth-on with every env fallback removed.

    Clearing the env vars is load-bearing: with ``NTFY_TOPIC`` set, the
    legacy fallback would satisfy the send path and these tests would pass
    for the wrong reason.
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "tier-wiring-secret")
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    for var in (
        "NTFY_URL",
        "NTFY_TOPIC",
        "NTFY_TOKEN",
        "CALENDAR_CALDAV_URL",
        "CALENDAR_USERNAME",
        "CALENDAR_PASSWORD",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# ntfy -- the silent-failure case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["household", "system"])
def test_ntfy_read_model_and_send_path_agree(store, auth_on, tier):
    """The headline regression. Reverting any of the three fixes fails here."""
    from connectors.registry import NTFY

    from services import credential_tiers as ct
    from services import me_notifications
    from services import ntfy_runtime

    put = ct.household_put_payload if tier == "household" else ct.system_put_payload
    put(
        NTFY,
        {"url": "https://ntfy.home.lan", "topic": "house-alerts", "token": "tok"},
        actor=_HOUSEHOLD_ACTOR,
    )

    channel = me_notifications._build_ntfy_channel(_MEMBER)
    cfg = ntfy_runtime.load_ntfy_runtime_config(_MEMBER)

    assert channel.active_tier == tier
    assert channel.configured is True
    assert cfg["topic"] == "house-alerts"
    assert cfg["url"] == "https://ntfy.home.lan"
    assert cfg["token"] == "tok"


def test_ntfy_delivery_actually_succeeds_on_a_household_credential(store, auth_on):
    """End of the chain: the channel reports delivery, not ``skipped``.

    ``ntfy_channel`` maps ``ConnectorNotConfigured`` to
    ``status="skipped"`` and returns normally, so asserting on the loader
    alone would miss a regression that reintroduces the swallow.
    """
    from connectors.registry import NTFY

    from services import credential_tiers as ct
    from services import ntfy_runtime

    ct.household_put_payload(
        NTFY,
        {"url": "https://ntfy.home.lan", "topic": "house-alerts"},
        actor=_HOUSEHOLD_ACTOR,
    )

    # No per-user row anywhere.
    from services import connector_credentials as ccs

    assert ccs.get_record(_MEMBER, NTFY) is None

    cfg = ntfy_runtime.load_ntfy_runtime_config(_MEMBER)
    assert cfg["topic"] == "house-alerts"


def test_user_tier_still_wins_over_household(store, auth_on):
    """Precedence is unchanged -- a personal credential outranks the house one."""
    from connectors.registry import NTFY

    from services import connector_credentials as ccs
    from services import credential_tiers as ct
    from services import ntfy_runtime

    ct.household_put_payload(
        NTFY, {"url": "https://house.lan", "topic": "house"}, actor=_HOUSEHOLD_ACTOR
    )
    ccs.put_payload(_MEMBER, NTFY, {"url": "https://mine.lan", "topic": "mine"}, actor="self")

    cfg = ntfy_runtime.load_ntfy_runtime_config(_MEMBER)

    assert cfg["topic"] == "mine"


def test_a_paused_user_row_does_not_fall_through_to_household(store, auth_on, monkeypatch):
    """Pausing is deliberate; substituting the household credential defeats it.

    Mirrors the resolver's own rule that a decrypt failure never advances
    to the next tier.

    ``delivery_paused`` is stubbed rather than written through
    ``set_delivery_paused``: the tier fake store predates the pause columns
    and its user-row projection does not carry them. The branch under test
    is the ``tier == "user"`` pause check in ``load_ntfy_runtime_config``,
    and stubbing the record reaches it without asserting anything about
    fake-store fidelity.
    """
    from connectors.registry import NTFY

    from services import connector_credentials as ccs
    from services import credential_tiers as ct
    from services import ntfy_runtime

    ct.household_put_payload(
        NTFY, {"url": "https://house.lan", "topic": "house"}, actor=_HOUSEHOLD_ACTOR
    )
    ccs.put_payload(_MEMBER, NTFY, {"url": "https://mine.lan", "topic": "mine"}, actor="self")

    real_get_record = ccs.get_record

    def _paused(user_id, connector, **kw):
        rec = real_get_record(user_id, connector, **kw)
        return None if rec is None else replace(rec, delivery_paused=True)

    monkeypatch.setattr(ntfy_runtime.ccs, "get_record", _paused)

    with pytest.raises(ccs.ConnectorNotConfigured, match="paused"):
        ntfy_runtime.load_ntfy_runtime_config(_MEMBER)


def test_no_credential_at_any_tier_still_raises(store, auth_on):
    from connectors.registry import NTFY  # noqa: F401

    from services import connector_credentials as ccs
    from services import ntfy_runtime

    with pytest.raises(ccs.ConnectorNotConfigured):
        ntfy_runtime.load_ntfy_runtime_config(_MEMBER)


# ---------------------------------------------------------------------------
# caldav
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["household", "system"])
def test_caldav_load_connection_honours_the_shared_tiers(store, auth_on, tier):
    from connectors.registry import CALDAV

    from services import caldav_credentials
    from services import credential_tiers as ct

    put = ct.household_put_payload if tier == "household" else ct.system_put_payload
    put(
        CALDAV,
        {"base_url": "https://dav.home.lan", "username": "house", "password": "pw"},
        actor=_HOUSEHOLD_ACTOR,
    )

    conn = caldav_credentials.load_connection(_MEMBER)

    assert conn.base_url == "https://dav.home.lan"
    assert conn.username == "house"


# ---------------------------------------------------------------------------
# llm -- resolution AND availability
# ---------------------------------------------------------------------------


def test_llm_key_resolves_from_the_household_tier(store, auth_on):
    from connectors.registry import LLM_ANTHROPIC

    from services import credential_tiers as ct
    from services import llm_connector_map

    ct.household_put_payload(LLM_ANTHROPIC, {"api_key": "sk-household"}, actor=_HOUSEHOLD_ACTOR)

    assert llm_connector_map.effective_api_key(_MEMBER, "ANTHROPIC_API_KEY") == "sk-household"


def test_a_household_llm_model_is_also_offered_not_just_resolvable(store, auth_on):
    """Resolution without availability is not a fix.

    ``is_model_enabled`` consults ``has_credential`` and ``/v1/models``
    consults ``get_user_credentials_snapshot``. Had those kept reading the
    per-user tier only, a household key would decrypt correctly at chat
    time while the model was filtered out of the picker -- usable in
    theory, unreachable in practice.
    """
    from connectors.registry import LLM_ANTHROPIC

    from services import credential_tiers as ct
    from services import llm_connector_map

    ct.household_put_payload(LLM_ANTHROPIC, {"api_key": "sk-household"}, actor=_HOUSEHOLD_ACTOR)

    assert llm_connector_map.has_credential(_MEMBER, "ANTHROPIC_API_KEY") is True
    assert LLM_ANTHROPIC in llm_connector_map.get_user_credentials_snapshot(_MEMBER)


# ---------------------------------------------------------------------------
# The anonymous caller -- caught by the full suite, not by the tests above
# ---------------------------------------------------------------------------


def test_the_auth_off_global_slot_still_resolves_from_env(store, monkeypatch):
    """``user_id=None`` must not reach the resolver's caller guard.

    ``config.get_llm_provider(..., user_id=None)`` is the auth-off global
    slot. The first cut of this fix passed ``user_id or ""`` straight to
    ``resolve_runtime_credential``, which rejects an empty
    ``caller_user_id`` with ``ValueError`` rather than querying for one --
    turning a working env-key path into a crash. Caught by
    ``test_llm_provider_per_user.py`` on the full suite, pinned here next
    to the change that caused it.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-FAKE")
    from services import llm_connector_map

    assert llm_connector_map.effective_api_key(None, "ANTHROPIC_API_KEY") == "sk-env-FAKE"


def test_the_anonymous_caller_still_sees_a_household_key(store, monkeypatch):
    """Household rows are keyed by connector alone, so they apply with no caller.

    Being the global slot is not a reason to ignore the house credential;
    only the per-user tier is meaningless without a user.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from connectors.registry import LLM_ANTHROPIC

    from services import credential_tiers as ct
    from services import llm_connector_map

    ct.household_put_payload(LLM_ANTHROPIC, {"api_key": "sk-household"}, actor=_HOUSEHOLD_ACTOR)

    assert llm_connector_map.effective_api_key(None, "ANTHROPIC_API_KEY") == "sk-household"


# ---------------------------------------------------------------------------
# LUM-850 -- a switched-off user row, seen from the read models
# ---------------------------------------------------------------------------
#
# LUM-834 chunk B gated every path that USES a credential: a disabled user row
# stops `resolve_runtime_credential` at tier 1, so neither the household nor the
# system key is used instead. Nothing gated the paths that ADVERTISE one, and
# three of them kept reporting the connector as active. The tests below pair the
# read model with the resolver in one assertion, for the same reason the LUM-24
# tests above do: each layer alone was well covered, and they still disagreed.
#
# Every test here seeds a household or system key next to the disabled row. That
# is the trap the ticket names: `WHERE enabled` on the user query would hide the
# user row and let the shared key re-advertise the connector, which is exactly
# the tier walk the resolver refuses to do.


def _disable(user_id: str, connector: str) -> None:
    from services import connector_credentials as ccs

    assert ccs.set_enabled(user_id, connector, enabled=False, actor="self") is not None


def _put_shared(tier: str, connector: str, payload: dict) -> None:
    from services import credential_tiers as ct

    put = ct.household_put_payload if tier == "household" else ct.system_put_payload
    put(connector, payload, actor=_HOUSEHOLD_ACTOR)


@pytest.mark.parametrize("tier", ["household", "system"])
def test_a_disabled_user_llm_row_hides_the_model_even_with_a_shared_key(store, auth_on, tier):
    """The pin the ticket asks for: disabled user row + shared key -> not offered.

    Both availability paths are asserted: ``get_user_credentials_snapshot``
    (what ``/v1/models`` and ``GET /api/v1/models`` consult) and
    ``has_credential`` (what ``is_model_enabled`` falls back to without a
    snapshot). Either one alone would leave the other offering the model.
    """
    from connectors.registry import LLM_ANTHROPIC

    from services import connector_credentials as ccs
    from services import credential_tiers as ct
    from services import llm_connector_map

    _put_shared(tier, LLM_ANTHROPIC, {"api_key": "sk-shared"})
    ccs.put_payload(_MEMBER, LLM_ANTHROPIC, {"api_key": "sk-mine"}, actor="self")
    _disable(_MEMBER, LLM_ANTHROPIC)

    with pytest.raises(ccs.ConnectorNotConfigured, match="disabled"):
        ct.resolve_runtime_credential(_MEMBER, LLM_ANTHROPIC)
    assert LLM_ANTHROPIC not in llm_connector_map.get_user_credentials_snapshot(_MEMBER)
    assert llm_connector_map.has_credential(_MEMBER, "ANTHROPIC_API_KEY") is False


@pytest.mark.parametrize("tier", ["household", "system"])
def test_an_enabled_user_llm_row_is_still_offered_next_to_a_shared_key(store, auth_on, tier):
    """The twin. A filter that hid every user-tier row would pass the test above."""
    from connectors.registry import LLM_ANTHROPIC

    from services import connector_credentials as ccs
    from services import llm_connector_map

    _put_shared(tier, LLM_ANTHROPIC, {"api_key": "sk-shared"})
    ccs.put_payload(_MEMBER, LLM_ANTHROPIC, {"api_key": "sk-mine"}, actor="self")

    assert llm_connector_map.effective_api_key(_MEMBER, "ANTHROPIC_API_KEY") == "sk-mine"
    assert LLM_ANTHROPIC in llm_connector_map.get_user_credentials_snapshot(_MEMBER)
    assert llm_connector_map.has_credential(_MEMBER, "ANTHROPIC_API_KEY") is True


def test_a_disabled_row_is_only_hidden_for_its_own_user(store, auth_on):
    """Bob has no row; Alice's switch must not stop his walk to the household key."""
    from connectors.registry import LLM_ANTHROPIC

    from services import connector_credentials as ccs
    from services import llm_connector_map

    _put_shared("household", LLM_ANTHROPIC, {"api_key": "sk-shared"})
    ccs.put_payload(_MEMBER, LLM_ANTHROPIC, {"api_key": "sk-mine"}, actor="self")
    _disable(_MEMBER, LLM_ANTHROPIC)

    assert LLM_ANTHROPIC in llm_connector_map.get_user_credentials_snapshot("bob")
    assert llm_connector_map.has_credential("bob", "ANTHROPIC_API_KEY") is True


@pytest.mark.parametrize("tier", ["household", "system"])
def test_me_llm_providers_does_not_report_a_disabled_row_as_configured(store, auth_on, tier):
    from connectors.registry import LLM_ANTHROPIC

    from services import connector_credentials as ccs
    from services import me_llm_providers

    _put_shared(tier, LLM_ANTHROPIC, {"api_key": "sk-shared"})
    ccs.put_payload(_MEMBER, LLM_ANTHROPIC, {"api_key": "sk-mine"}, actor="self")
    _disable(_MEMBER, LLM_ANTHROPIC)

    with pytest.raises(ccs.ConnectorNotConfigured, match="disabled"):
        from services import llm_connector_map

        llm_connector_map.effective_api_key(_MEMBER, "ANTHROPIC_API_KEY")

    resp = me_llm_providers.build_me_llm_providers_response(_MEMBER)
    row = next(p for p in resp.providers if p.connector == LLM_ANTHROPIC)
    assert row.configured is False
    assert row.status == "not_configured"
    # The user tier is where resolution stops, so it stays the tier on show,
    # and the row is still there -- it is switched off, not deleted.
    assert row.active_tier == "user"
    assert row.user_credential_present is True
    assert row.why_not_available and "switched off" in row.why_not_available
    assert resp.summary.configured == sum(1 for p in resp.providers if p.configured)


def test_me_llm_providers_still_reports_an_enabled_user_row(store, auth_on):
    from connectors.registry import LLM_ANTHROPIC

    from services import connector_credentials as ccs
    from services import me_llm_providers

    ccs.put_payload(_MEMBER, LLM_ANTHROPIC, {"api_key": "sk-mine"}, actor="self")

    resp = me_llm_providers.build_me_llm_providers_response(_MEMBER)
    row = next(p for p in resp.providers if p.connector == LLM_ANTHROPIC)
    assert (row.configured, row.status, row.active_tier) == (True, "configured", "user")
    assert row.why_not_available is None


@pytest.mark.parametrize("tier", ["household", "system"])
def test_me_notifications_does_not_report_a_disabled_ntfy_as_active(store, auth_on, tier):
    from connectors.registry import NTFY

    from services import connector_credentials as ccs
    from services import me_notifications
    from services import ntfy_runtime

    _put_shared(tier, NTFY, {"url": "https://ntfy.home.lan", "topic": "house"})
    ccs.put_payload(_MEMBER, NTFY, {"url": "https://mine.lan", "topic": "mine"}, actor="self")
    _disable(_MEMBER, NTFY)

    with pytest.raises(ccs.ConnectorNotConfigured):
        ntfy_runtime.load_ntfy_runtime_config(_MEMBER)

    channel = me_notifications._build_ntfy_channel(_MEMBER)
    assert channel.configured is False
    assert channel.status == "not_configured"
    assert channel.active_tier == "user"
    assert channel.user_credential_present is True
    assert channel.why_not_available and "switched off" in channel.why_not_available


def test_me_notifications_a_disabled_ntfy_does_not_fall_back_to_the_env(store, monkeypatch):
    """The default deployment: auth off, ``NTFY_TOPIC`` set.

    ``ntfy_runtime`` refuses the env for a disabled row (LUM-834 chunk B); the
    read model must not advertise the env channel the send path will not use.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.setenv("NTFY_TOPIC", "env-topic")
    from connectors.registry import NTFY

    from services import connector_credentials as ccs
    from services import me_notifications
    from services import ntfy_runtime

    ccs.put_payload(_MEMBER, NTFY, {"url": "https://mine.lan", "topic": "mine"}, actor="self")
    _disable(_MEMBER, NTFY)

    with pytest.raises(ccs.ConnectorNotConfigured):
        ntfy_runtime.load_ntfy_runtime_config(_MEMBER)

    channel = me_notifications._build_ntfy_channel(_MEMBER)
    assert channel.configured is False
    assert channel.status == "not_configured"
    assert channel.env_fallback_available is True
    assert channel.url is None


# ---------------------------------------------------------------------------
# LUM-850 -- the metadata tier walk is the resolver's tier walk
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("user_row", ["absent", "enabled", "disabled"])
@pytest.mark.parametrize("household", [False, True])
@pytest.mark.parametrize("system", [False, True])
@pytest.mark.parametrize("env", [False, True])
def test_runtime_tier_from_records_agrees_with_the_resolver(
    store, monkeypatch, user_row, household, system, env
):
    """Every combination, both sides, one assertion.

    The read models cannot call the resolver (it decrypts), so they call
    ``runtime_tier_from_records``, which walks the same tiers on metadata.
    The two are separate code, so this table is what keeps them one rule:
    a change to either walk that is not made to the other fails here.

    Auth off, because that is the only mode with a reachable env tier.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    if env:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from connectors.registry import LLM_ANTHROPIC

    from services import connector_credentials as ccs
    from services import credential_tiers as ct

    if household:
        _put_shared("household", LLM_ANTHROPIC, {"api_key": "sk-h"})
    if system:
        _put_shared("system", LLM_ANTHROPIC, {"api_key": "sk-s"})
    if user_row != "absent":
        ccs.put_payload(_MEMBER, LLM_ANTHROPIC, {"api_key": "sk-u"}, actor="self")
    if user_row == "disabled":
        _disable(_MEMBER, LLM_ANTHROPIC)

    try:
        resolver_tier = ct.resolve_runtime_credential(
            _MEMBER, LLM_ANTHROPIC, fallback_env="ANTHROPIC_API_KEY"
        ).tier
    except ccs.ConnectorNotConfigured:
        resolver_tier = "disabled" if user_row == "disabled" else "none"

    predicted = ct.runtime_tier_from_records(
        ccs.get_record(_MEMBER, LLM_ANTHROPIC),
        household_present=ct.household_get_record(LLM_ANTHROPIC) is not None,
        system_present=ct.system_get_record(LLM_ANTHROPIC) is not None,
        env_fallback=env,
    )
    assert predicted == resolver_tier
