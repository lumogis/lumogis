# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Opt-in contradiction-judgment eval runner (LUM-104 Stage-2 gate artefact).

Runs the REAL judge model against every hand-graded pair in ``pairs.jsonl``
and emits a precision/recall/confusion report as a JSON artefact.

Opt-in discipline (mirrors ``web-e2e-ollama-prove``): skipped unless
``LUMOGIS_TEMPORAL_EVAL=1`` and an Ollama endpoint is reachable. The pass
bar gates the AUTO-APPLY recommendation, not the merge: this test asserts
the runner executes and emits a report — bar-meeting is a Stage-2 release
action, evaluated by a human from the committed report.

Pass bar (checkpoint decision a — measured against ONE reference judge
model, recorded in the report; does not transfer to a different judge):

* point precision of ``supersedes`` >= 0.90, AND
* Wilson 95% lower bound of that precision >= 0.70, AND
* the fixture contains >= 20 expected-``supersedes`` rows
  (below the floor the runner REFUSES to emit a gating verdict).

Precision denominator = rows the judge PREDICTED ``supersedes`` over the
whole fixture. Recall is reported (informational target >= 0.50, not
gating). valid_at extraction accuracy (±1 day) is reported informationally.

Fixture status: the drafted rows require Thomas's hand-grading before the
eval is authoritative (grading is the human checkpoint's tail).

Interfaces kept stable for LUM-114 / LUM-532: the JSONL row shape and the
report format below.
"""

from __future__ import annotations

import json
import math
import os
import urllib.request
from pathlib import Path

import pytest

_PAIRS_PATH = Path(__file__).resolve().parent / "pairs.jsonl"
_MIN_POSITIVE_N = 20
_PRECISION_BAR = 0.90
_WILSON_LB_BAR = 0.70
_RECALL_TARGET_INFORMATIONAL = 0.50


# ---------------------------------------------------------------------------
# Metric helpers (unit-tested below without any LLM)
# ---------------------------------------------------------------------------


def wilson_lower_bound(successes: int, n: int, z: float = 1.96) -> float:
    """95% Wilson score lower bound for a binomial proportion."""
    if n <= 0:
        return 0.0
    phat = successes / n
    denom = 1.0 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1.0 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def compute_metrics(rows: list[dict]) -> dict:
    """Compute the gating metrics from ``[{expected, predicted}, ...]`` rows.

    ``gating_verdict`` is ``"refused"`` when the fixture's positive count is
    below the minimum-N floor — a 0.90 point precision over a handful of
    positives is statistically meaningless.
    """
    positives = [r for r in rows if r["expected"] == "supersedes"]
    predicted_pos = [r for r in rows if r["predicted"] == "supersedes"]
    tp = sum(1 for r in predicted_pos if r["expected"] == "supersedes")
    precision = tp / len(predicted_pos) if predicted_pos else 0.0
    recall = tp / len(positives) if positives else 0.0
    lb = wilson_lower_bound(tp, len(predicted_pos))
    confusion: dict[str, dict[str, int]] = {}
    for r in rows:
        confusion.setdefault(r["expected"], {}).setdefault(r["predicted"], 0)
        confusion[r["expected"]][r["predicted"]] += 1
    if len(positives) < _MIN_POSITIVE_N:
        verdict = "refused"
        reason = (
            f"positive-N {len(positives)} < floor {_MIN_POSITIVE_N} — no gating verdict emitted"
        )
    elif precision >= _PRECISION_BAR and lb >= _WILSON_LB_BAR:
        verdict = "pass"
        reason = "precision and Wilson lower bound meet the bar"
    else:
        verdict = "fail"
        reason = "below the bar — auto-apply recommendation stays OFF (shadow mode)"
    return {
        "rows": len(rows),
        "expected_supersedes": len(positives),
        "predicted_supersedes": len(predicted_pos),
        "true_positives": tp,
        "precision_supersedes": round(precision, 4),
        "wilson_lb_95": round(lb, 4),
        "recall_supersedes": round(recall, 4),
        "recall_informational_target": _RECALL_TARGET_INFORMATIONAL,
        "min_positive_n": _MIN_POSITIVE_N,
        "precision_bar": _PRECISION_BAR,
        "wilson_lb_bar": _WILSON_LB_BAR,
        "confusion": confusion,
        "gating_verdict": verdict,
        "gating_reason": reason,
    }


def load_pairs() -> list[dict]:
    rows = []
    with open(_PAIRS_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Fixture-integrity + metric unit tests (always run — no LLM)
# ---------------------------------------------------------------------------


def test_fixture_meets_minimum_positive_n():
    rows = load_pairs()
    assert len(rows) >= 50
    positives = [r for r in rows if r["expected_verdict"] == "supersedes"]
    assert len(positives) >= 22, "eval gate is under-powered below 22 positives"
    for row in rows:
        assert {
            "id",
            "existing_fact_text",
            "existing_fact_pair",
            "episode_text",
            "reference_time",
            "expected_verdict",
            "notes",
        } <= set(row)
        assert row["expected_verdict"] in ("supersedes", "compatible", "unrelated")


def test_runner_refuses_gating_verdict_below_positive_floor():
    rows = [{"expected": "supersedes", "predicted": "supersedes"}] * 5 + [
        {"expected": "compatible", "predicted": "compatible"}
    ] * 10
    metrics = compute_metrics(rows)
    assert metrics["gating_verdict"] == "refused"


def test_point_precision_alone_does_not_pass_without_wilson_lb():
    # 21/22 correct predictions on 22 positives: precision 0.954, LB ~0.78 → pass
    rows = (
        [{"expected": "supersedes", "predicted": "supersedes"}] * 21
        + [{"expected": "compatible", "predicted": "supersedes"}]
        + [{"expected": "supersedes", "predicted": "compatible"}]
    )
    m = compute_metrics(rows)
    assert m["expected_supersedes"] == 22
    assert m["precision_supersedes"] >= 0.9
    assert m["gating_verdict"] == "pass"
    # 9/10 predicted-supersedes over 22 positives: precision 0.9 but LB < 0.70 → fail
    rows2 = (
        [{"expected": "supersedes", "predicted": "supersedes"}] * 9
        + [{"expected": "compatible", "predicted": "supersedes"}]
        + [{"expected": "supersedes", "predicted": "compatible"}] * 13
    )
    m2 = compute_metrics(rows2)
    assert m2["precision_supersedes"] == 0.9
    assert m2["wilson_lb_95"] < 0.70
    assert m2["gating_verdict"] == "fail"


def test_wilson_lower_bound_sane():
    assert wilson_lower_bound(0, 0) == 0.0
    assert 0.55 < wilson_lower_bound(19, 20) < 0.95
    assert wilson_lower_bound(19, 20) < 19 / 20


# ---------------------------------------------------------------------------
# Opt-in live eval (real judge model against every pair)
# ---------------------------------------------------------------------------


def _ollama_reachable() -> bool:
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    try:
        with urllib.request.urlopen(url, timeout=3):
            return True
    except Exception:
        return False


_eval_enabled = os.environ.get("LUMOGIS_TEMPORAL_EVAL", "") == "1"


@pytest.mark.skipif(
    not _eval_enabled or not _ollama_reachable(),
    reason="opt-in: set LUMOGIS_TEMPORAL_EVAL=1 with a reachable Ollama endpoint",
)
def test_contradiction_eval_runs_and_emits_report(monkeypatch):
    """Run the real judge over the fixture; emit the JSON report artefact.

    Bar-meeting is NOT asserted (Stage-2 gate, not a merge gate) — the
    report's ``gating_verdict`` is the human-review artefact.
    """
    from graph import temporal
    from plugins.graph import temporal_pipeline as tp

    monkeypatch.setenv("LUMOGIS_FF_TEMPORAL_KG", "true")
    judge_model = tp._judge_model("eval")
    pairs = load_pairs()
    results: list[dict] = []
    for row in pairs:
        candidate = {
            "edge_uid": row["id"],
            "a_id": row["existing_fact_pair"][0],
            "b_id": row["existing_fact_pair"][1],
            "scope_tier": "personal",
            "fact_texts": [row["existing_fact_text"]],
            "relation_types": [],
            "superseded_evidence": None,
        }
        ref = temporal.parse_graph_ts(row["reference_time"])
        verdicts = tp.judge_contradictions(row["episode_text"], [candidate], "eval", ref)
        predicted = verdicts[0].verdict if verdicts else "judge_failed"
        results.append(
            {
                "id": row["id"],
                "expected": row["expected_verdict"],
                "predicted": predicted,
                "confidence": verdicts[0].confidence if verdicts else None,
                "reason": verdicts[0].reason if verdicts else None,
            }
        )

    metrics = compute_metrics(
        [{"expected": r["expected"], "predicted": r["predicted"]} for r in results]
    )
    report = {
        "reference_judge_model": judge_model,
        "fixture": str(_PAIRS_PATH.name),
        "generated_by": "test_contradiction_eval.py",
        "metrics": metrics,
        "per_pair": results,
    }
    out_path = Path(
        os.environ.get(
            "LUMOGIS_TEMPORAL_EVAL_REPORT",
            str(_PAIRS_PATH.parent / "eval-report.json"),
        )
    )
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"\ntemporal eval: model={judge_model} precision={metrics['precision_supersedes']} "
        f"wilson_lb={metrics['wilson_lb_95']} recall={metrics['recall_supersedes']} "
        f"verdict={metrics['gating_verdict']} report={out_path}"
    )
    assert out_path.is_file()  # the artefact IS the deliverable
