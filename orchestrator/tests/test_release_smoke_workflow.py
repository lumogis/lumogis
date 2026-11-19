# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Static assertions over release-smoke.yml and release-assets.yml (LUM-997).

The structure is the gate: ``publish`` needs ``[stage, smoke]`` with default success
semantics, so these tests assert shapes (equality, absence of ``always()``), not
substrings that a rewording would slip past.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_ROOT = Path(__file__).resolve().parent.parent.parent
_WF = _ROOT / ".github" / "workflows"
GUARD = "github.repository == 'lumogis/lumogis' && github.ref == 'refs/heads/main'"


def _load(name: str) -> dict:
    doc = yaml.safe_load((_WF / name).read_text(encoding="utf-8"))
    # PyYAML reads the key `on` as the boolean True.
    if True in doc and "on" not in doc:
        doc["on"] = doc.pop(True)
    return doc


SMOKE = _load("release-smoke.yml")
ASSETS = _load("release-assets.yml")
SMOKE_TEXT = (_WF / "release-smoke.yml").read_text(encoding="utf-8")
ASSETS_TEXT = (_WF / "release-assets.yml").read_text(encoding="utf-8")


def _steps(job: dict) -> list[dict]:
    return job.get("steps", []) or []


def _all_uses(doc: dict) -> list[str]:
    return [s["uses"] for j in doc["jobs"].values() for s in _steps(j) if "uses" in s]


# ---------------------------------------------------------------- release-smoke.yml


def test_smoke_jobs_guarded_on_repo_and_main():
    for name, job in SMOKE["jobs"].items():
        cond = str(job.get("if", ""))
        assert "github.repository == 'lumogis/lumogis'" in cond, name
        assert "refs/heads/main" in cond, name


def test_smoke_has_no_pr_triggers():
    assert set(SMOKE["on"]) <= {"workflow_call", "workflow_dispatch"}


def test_smoke_permissions_read_only():
    assert SMOKE["permissions"] == {}
    for name, job in SMOKE["jobs"].items():
        assert job["permissions"] == {"contents": "read"}, name


def test_smoke_matrix_runs_both_arches():
    m = SMOKE["jobs"]["smoke"]["strategy"]
    assert m["fail-fast"] is False
    assert m["matrix"]["runner"] == ["ubuntu-24.04", "ubuntu-24.04-arm"]


def test_smoke_runner_matrix_is_static_and_suite_matrix_dynamic():
    job = SMOKE["jobs"]["smoke"]
    assert isinstance(job["strategy"]["matrix"]["runner"], list)
    assert "fromJSON" in str(job["strategy"]["matrix"]["suite"])
    for name, j in SMOKE["jobs"].items():
        assert "matrix." not in str(j.get("if", "")), (
            f"{name}: `matrix` is not available in a job `if:`"
        )


def test_smoke_third_party_actions_sha_pinned():
    for uses in _all_uses(SMOKE) + _all_uses(ASSETS):
        if uses.startswith("./"):
            continue
        assert re.search(r"@[0-9a-f]{40}$", uses), uses


def test_smoke_never_inherits_secrets():
    assert not re.search(r"secrets:\s*inherit", SMOKE_TEXT + ASSETS_TEXT)


def test_summary_runs_always():
    job = SMOKE["jobs"]["summary"]
    assert "always()" in job["if"] and GUARD in job["if"]
    assert set(job["needs"]) == {"files", "smoke", "smoke-cloud-llm"}


def test_smoke_checks_out_workflow_commit_not_tag():
    for name, job in SMOKE["jobs"].items():
        for step in _steps(job):
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                ref = str((step.get("with") or {}).get("ref", ""))
                assert "refs/tags" not in ref and "inputs.version" not in ref, name


def test_smoke_never_runs_compose_logs_in_workflow():
    for job in SMOKE["jobs"].values():
        for step in _steps(job):
            assert "compose logs" not in str(step.get("run", ""))


def test_only_script_step_continues_on_error():
    for name, job in SMOKE["jobs"].items():
        assert "continue-on-error" not in job, name
        for step in _steps(job):
            if step.get("continue-on-error"):
                label = str(step.get("name", ""))
                assert label in {"Smoke suite", "Free disk space"}, f"{name}: {label}"


def test_no_job_level_secret_env():
    for name, job in SMOKE["jobs"].items():
        assert "secrets." not in str(job.get("env", {})), name
        for step in _steps(job):
            if "secrets." in str(step):
                assert step.get("name") == "Smoke suite", f"{name}: {step.get('name')}"
    assert "SMOKE_CLOUD_LLM_KEY" not in str(SMOKE["jobs"]["smoke"])
    assert "SMOKE_CLOUD_LLM_KEY" in str(SMOKE["jobs"]["smoke-cloud-llm"])


def test_every_upload_artifact_overwrites():
    for doc in (SMOKE, ASSETS):
        for job in doc["jobs"].values():
            for step in _steps(job):
                if str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                    assert step["with"]["overwrite"] is True


def test_no_concurrency_in_release_smoke():
    assert "concurrency" not in SMOKE
    assert all("concurrency" not in j for j in SMOKE["jobs"].values())


def test_cloud_llm_job_runs_on_amd64_only():
    assert SMOKE["jobs"]["smoke-cloud-llm"]["runs-on"] == "ubuntu-24.04"


def test_full_suite_list_on_call():
    plan = next(s for s in _steps(SMOKE["jobs"]["files"]) if s.get("id") == "plan")["run"]
    assert 'list="install,graph,upgrade,secure-address,cloud-llm"' in plan
    assert "cloud-llm@amd64" in plan


# ---------------------------------------------------------------- release-assets.yml


def test_release_assets_publishes_only_after_smoke():
    jobs = ASSETS["jobs"]
    assert set(jobs) == {"stage", "smoke", "publish"}
    assert jobs["publish"]["needs"] == ["stage", "smoke"]
    assert jobs["publish"]["if"] == GUARD
    assert jobs["smoke"]["if"] == GUARD
    for name, job in jobs.items():
        cond = str(job.get("if", ""))
        for banned in ("always()", "failure()", "cancelled()", "needs."):
            assert banned not in cond, f"{name}: {banned}"
        assert "continue-on-error" not in job, name


def test_release_assets_upload_only_in_publish():
    for name in ("stage", "smoke"):
        text = str(ASSETS["jobs"][name])
        assert "gh release upload" not in text and "gh release edit" not in text, name
    steps = [s.get("name", s.get("uses", "")) for s in _steps(ASSETS["jobs"]["publish"])]
    up = steps.index("Upload the files to the Release")
    assert up > steps.index("The files are the ones that were smoked")
    assert steps.index("The files are the ones that were smoked") > 0


def test_release_assets_skip_reason_passed_to_smoke_and_validated():
    smoke = ASSETS["jobs"]["smoke"]
    assert smoke["with"]["skip_reason"] == "${{ inputs.skip_smoke_reason }}"
    assert smoke["uses"] == "./.github/workflows/release-smoke.yml"
    validate = next(
        s for s in _steps(ASSETS["jobs"]["stage"]) if s.get("name") == "Validate inputs"
    )
    assert "LUM-[0-9]+" in validate["run"]
    assert "skip_smoke_reason" in ASSETS["on"]["workflow_dispatch"]["inputs"]


def test_release_assets_verifies_moved_tags():
    steps = [s.get("name") for s in _steps(ASSETS["jobs"]["publish"])]
    assert steps.index("Check the moved tags anonymously") > steps.index(
        "Move the release and X.Y tags"
    )
    check = next(
        s
        for s in _steps(ASSETS["jobs"]["publish"])
        if s.get("name") == "Check the moved tags anonymously"
    )
    assert (
        "imagetools inspect" in check["run"]
        and "release" in check["run"]
        and "$MINOR" in check["run"]
    )


def test_release_assets_tag_moves_and_publish_only_in_publish_job():
    for name in ("stage", "smoke"):
        text = str(ASSETS["jobs"][name])
        assert "imagetools create" not in text and "--draft=false" not in text, name
    publish = str(ASSETS["jobs"]["publish"])
    assert "imagetools create" in publish and "--draft=false" in publish


def test_release_assets_single_attest_in_stage_without_if():
    attests = [
        (n, s)
        for n, j in ASSETS["jobs"].items()
        for s in _steps(j)
        if str(s.get("uses", "")).startswith("actions/attest-build-provenance@")
    ]
    assert len(attests) == 1 and attests[0][0] == "stage"
    assert "if" not in attests[0][1] and "continue-on-error" not in attests[0][1]


def test_release_assets_stage_has_no_packages_write():
    perms = ASSETS["jobs"]["stage"]["permissions"]
    assert perms["packages"] == "read"
    assert ASSETS["jobs"]["publish"]["permissions"]["packages"] == "write"
    assert ASSETS["jobs"]["smoke"]["permissions"] == {"contents": "read"}


def test_release_assets_uploads_files_artifact_named_for_smoke():
    up = next(
        s
        for s in _steps(ASSETS["jobs"]["stage"])
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert up["with"]["name"] == "release-files-${{ inputs.version }}"
    assert ASSETS["jobs"]["smoke"]["with"]["files_artifact"] == up["with"]["name"]


def test_release_assets_checks_core_version_in_stage():
    names = [s.get("name") for s in _steps(ASSETS["jobs"]["stage"])]
    assert "Core reports the release version" in names
