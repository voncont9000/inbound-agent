from datetime import UTC, datetime
from pathlib import Path

from inbound_core.call_report import CallReport

from inbound_voice.call_report import build_call_report

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "eval-call"


def test_build_call_report_from_existing_eval_artifacts() -> None:
    ended_at = datetime(2026, 10, 18, tzinfo=UTC)

    report = build_call_report(FIXTURE_DIR, "external-call", ended_at)

    assert report.external_call_id == "external-call"
    assert report.ended_at == ended_at
    assert len(report.transcript) == 2
    assert report.transcript[0].timestamp == ended_at
    assert report.live_intake
    assert report.post_call_intake is not None
    assert report.merged_intake
    assert len(report.tasks) == 2
    assert report.end_reason is None
    assert report.metrics["call_duration_seconds"] == 120
    assert report.estimated_cost is None
    CallReport.model_validate(report.model_dump(mode="json"))


def test_report_uses_last_end_call_source_and_separates_estimated_cost(tmp_path: Path) -> None:
    (tmp_path / "tasks.json").write_text(
        '[{"type":"end_call","source":"idle"},{"type":"end_call","source":"goodbye_detected"}]',
        encoding="utf-8",
    )
    (tmp_path / "metrics.json").write_text(
        '{"estimated_cost":{"total":0.42},"call_duration_seconds":8}',
        encoding="utf-8",
    )

    report = build_call_report(tmp_path, "external-call")

    assert report.end_reason == "goodbye_detected"
    assert report.estimated_cost == {"total": 0.42}
    assert report.metrics == {"call_duration_seconds": 8}


def test_report_includes_trace_id_from_metrics_artifact(tmp_path: Path) -> None:
    trace_id = "0123456789abcdef0123456789abcdef"
    (tmp_path / "metrics.json").write_text(
        f'{{"trace_id":"{trace_id}","call_duration_seconds":8}}',
        encoding="utf-8",
    )

    report = build_call_report(tmp_path, "external-call")

    assert report.metrics["trace_id"] == trace_id
