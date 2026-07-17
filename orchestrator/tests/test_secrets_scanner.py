# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-361 — secrets scanner for user-authored config files."""

from __future__ import annotations

import pytest
from services.secrets_scanner import PatternSecretsScanner
from services.secrets_scanner import load_secret_rules
from services.secrets_scanner import remediation_message
from services.secrets_scanner import reset_cache_for_tests


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_cache_for_tests()
    yield
    reset_cache_for_tests()


# One representative positive per pattern id. Value shapes are synthetic, not
# real credentials.
POSITIVES = {
    "openai_api_key": "OPENAI=sk-" + "a" * 48,
    "anthropic_api_key": "key: sk-ant-api03-" + "A" * 80,
    "slack_bot_token": "hook: xox" + "b-123456789-abcdefABCDEF0123",
    "github_pat": "gh: ghp_" + "a" * 36,
    "github_s2s_token": "gh: ghs_" + "b" * 36,
    "google_api_key": "g: AIza" + "0" * 35,
    "aws_access_key_id": "aws: AKIA" + "IOSFODNN7EXAMPLE",
    "bearer_token": "Authorization: Bearer abcdefghij0123456789xyz",
    "hardcoded_password": 'password = "hunter2hunter2"',
    "hardcoded_api_key": 'api_key = "0123456789abcdef0123"',
    "hardcoded_token": 'token = "0123456789abcdef0123"',
    "private_key_block": "-----BEGIN " + "RSA PRIVATE KEY-----",
    "env_secret_assignment": "MY_SERVICE_TOKEN=abc123def456",
}


def test_all_14_patterns_defined():
    rules = load_secret_rules()
    assert len(rules) == 14, "acceptance criterion: 14 secret patterns defined"
    ids = {r.id for r in rules}
    assert len(ids) == 14, "pattern ids must be unique"


def test_uuid_pattern_is_defined_but_opt_in():
    rules = {r.id: r for r in load_secret_rules()}
    assert "uuid_token" in rules
    assert rules["uuid_token"].enabled is False, "UUID pattern ships disabled (false positives)"


@pytest.mark.parametrize("pattern_id,sample", sorted(POSITIVES.items()))
def test_each_pattern_blocks(pattern_id: str, sample: str):
    result = PatternSecretsScanner().scan(sample)
    assert result["blocked"] is True
    hit_ids = {m["pattern_id"] for m in result["matches"]}
    assert pattern_id in hit_ids, f"{pattern_id} should have matched {sample!r}"


def test_clean_config_is_not_blocked():
    clean = "---\nname: morning-brief\nschedule: daily\n---\nSummarise my calendar and inbox."
    result = PatternSecretsScanner().scan(clean)
    assert result["blocked"] is False
    assert result["matches"] == []


def test_env_assignment_placeholder_does_not_block():
    # Review #1: prose naming a credential var without a real value must not
    # block the file (short placeholder < 12 chars → no match).
    result = PatternSecretsScanner().scan("Never store your API_KEY = <vault> in this policy.")
    assert result["blocked"] is False


def test_env_assignment_real_value_blocks():
    result = PatternSecretsScanner().scan("MY_SERVICE_TOKEN=abc123def456ghi")
    ids = {m["pattern_id"] for m in result["matches"]}
    assert "env_secret_assignment" in ids


def test_disabled_uuid_pattern_does_not_block_by_default():
    # A bare UUID must not block while the pattern ships disabled.
    result = PatternSecretsScanner().scan("id: 550e8400-e29b-41d4-a716-446655440000")
    assert result["blocked"] is False


def test_line_number_is_reported():
    text = 'line one\nline two\napi_key = "0123456789abcdef0123"\nline four'
    result = PatternSecretsScanner().scan(text)
    assert result["blocked"] is True
    m = next(m for m in result["matches"] if m["pattern_id"] == "hardcoded_api_key")
    assert m["line"] == 3


def test_first_line_per_pattern_only():
    text = "ghp_" + "a" * 36 + "\n" + "ghp_" + "b" * 36
    result = PatternSecretsScanner().scan(text)
    hits = [m for m in result["matches"] if m["pattern_id"] == "github_pat"]
    assert len(hits) == 1 and hits[0]["line"] == 1


def test_empty_text_is_not_blocked():
    assert PatternSecretsScanner().scan("")["blocked"] is False


def test_result_never_carries_the_raw_secret():
    secret = "sk-" + "z" * 48
    result = PatternSecretsScanner().scan(f"OPENAI={secret}")
    # matches expose only id/name/line — never the matched value.
    for m in result["matches"]:
        assert set(m.keys()) == {"pattern_id", "name", "line"}
        assert secret not in str(m)


def test_remediation_message_names_pattern_and_line_not_value():
    secret = "ghp_" + "a" * 36
    result = PatternSecretsScanner().scan(f"gh: {secret}")
    msg = remediation_message(result)
    assert "GitHub personal access token" in msg
    assert "line 1" in msg
    assert secret not in msg


def test_remediation_message_empty_when_clean():
    assert remediation_message({"blocked": False, "matches": []}) == ""


def test_null_scanner_never_blocks():
    from adapters.null_secrets_scanner import NullSecretsScanner

    result = NullSecretsScanner().scan('api_key = "0123456789abcdef0123"')
    assert result["blocked"] is False
    assert result["matches"] == []


def test_config_returns_pattern_scanner_when_enabled(monkeypatch):
    import config

    monkeypatch.setenv("SECRETS_SCANNER_ENABLED", "true")
    config._instances.pop("secrets_scanner", None)
    scanner = config.get_secrets_scanner()
    assert type(scanner).__name__ == "PatternSecretsScanner"
    config._instances.pop("secrets_scanner", None)


def test_config_returns_null_scanner_when_disabled(monkeypatch):
    import config

    monkeypatch.setenv("SECRETS_SCANNER_ENABLED", "false")
    config._instances.pop("secrets_scanner", None)
    scanner = config.get_secrets_scanner()
    assert type(scanner).__name__ == "NullSecretsScanner"
    config._instances.pop("secrets_scanner", None)
