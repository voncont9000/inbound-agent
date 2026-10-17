from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any

import yaml

from inbound_voice.metrics import calculate_cost, load_price_table

VOICE_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_PATH = Path(__file__).with_name("scenarios.yaml")
PRICES_PATH = VOICE_ROOT / "config" / "prices.yaml"


def score_call(
    run_dir: str | Path,
    scenario: dict[str, Any],
    prices: dict[str, Any] | None = None,
) -> dict[str, Any]:
    directory = Path(run_dir)
    transcript_path = directory / "transcript.jsonl"
    transcript = []
    if transcript_path.exists():
        transcript = [
            json.loads(line)
            for line in transcript_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    user_text = " ".join(
        str(row.get("text", "")) for row in transcript if row.get("role") in {"caller", "user"}
    )
    intake = _read_json(directory / "intake.json", {})
    tasks = _read_json(directory / "tasks.json", [])
    metrics = _read_json(directory / "metrics.json", {})

    transcript_checks = {
        name: {
            "pattern": pattern,
            "passed": re.search(pattern, user_text, re.IGNORECASE) is not None,
        }
        for name, pattern in scenario.get("transcript_checks", {}).items()
    }
    fields = intake.get("fields", {}) if isinstance(intake, dict) else {}
    intake_checks = {}
    for field, pattern in scenario.get("intake_checks", {}).items():
        value = fields.get(field)
        intake_checks[field] = {
            "pattern": pattern,
            "value": value,
            "passed": (
                value is not None and re.search(pattern, str(value), re.IGNORECASE) is not None
            ),
        }

    task_checks = {}
    task_types = {task.get("type") for task in tasks if isinstance(task, dict)}
    for name, expected in scenario.get("task_checks", {}).items():
        actual = "urgent" in task_types if name == "urgent" else name in task_types
        task_checks[name] = {
            "expected": expected,
            "actual": actual,
            "passed": actual is expected,
        }

    checks = [*transcript_checks.values(), *intake_checks.values(), *task_checks.values()]
    price_table = prices if prices is not None else load_price_table(PRICES_PATH)
    cost = calculate_cost(metrics, price_table)
    service_metrics = metrics.get("service_metrics", [])
    provider_metadata = metrics.get("providers") or {}
    stt_ttfb = _ttfb_median(service_metrics, "stt")
    tts_ttfb = _ttfb_median(service_metrics, "tts")

    return {
        "call_id": metrics.get("call_id", directory.name),
        "run_dir": str(directory),
        "transcript_checks": transcript_checks,
        "intake_checks": intake_checks,
        "task_checks": task_checks,
        "checks_passed": sum(bool(check["passed"]) for check in checks),
        "checks_total": len(checks),
        "turn_latency_summary": metrics.get("turn_latency_summary", {}),
        "turn_end_latency_summary": metrics.get("turn_end_latency_summary", {}),
        "stt_ttfb_median_seconds": stt_ttfb,
        "tts_ttfb_median_seconds": tts_ttfb,
        "interruptions": metrics.get("interruption_count", metrics.get("interruptions")),
        "call_duration_seconds": metrics.get("call_duration_seconds"),
        "estimated_cost": cost,
        "ended_via_end_call": "end_call" in task_types,
        "tts_voice": provider_metadata.get("tts_voice"),
    }


def _ttfb_median(service_metrics: list[dict[str, Any]], kind: str) -> float | None:
    values = []
    service_marker = f"{kind}service"
    for metric in service_metrics:
        processor = str(metric.get("processor", "")).lower()
        if service_marker not in processor:
            continue
        value = metric.get("ttfb_seconds")
        if value is None and metric.get("kind") == "ttfb":
            value = metric.get("seconds")
        if value is not None:
            values.append(float(value))
    return statistics.median(values) if values else None


def aggregate_calls(calls: list[dict[str, Any]]) -> dict[str, Any]:
    latency_p50 = [
        call["turn_latency_summary"]["p50_seconds"]
        for call in calls
        if call.get("turn_latency_summary", {}).get("p50_seconds") is not None
    ]
    latency_p95 = [
        call["turn_latency_summary"]["p95_seconds"]
        for call in calls
        if call.get("turn_latency_summary", {}).get("p95_seconds") is not None
    ]
    costs = [
        call["estimated_cost"]["total"]
        for call in calls
        if call.get("estimated_cost", {}).get("total") is not None
    ]
    return {
        "calls": len(calls),
        "median_p50_latency_seconds": statistics.median(latency_p50) if latency_p50 else None,
        "max_p95_latency_seconds": max(latency_p95) if latency_p95 else None,
        "checks_passed": sum(call.get("checks_passed", 0) for call in calls),
        "checks_total": sum(call.get("checks_total", 0) for call in calls),
        "mean_cost": statistics.fmean(costs) if costs else None,
    }


def render_summary(calls: list[dict[str, Any]]) -> str:
    columns = (
        "Kind",
        "STT",
        "TTS",
        "Voice",
        "Scenario",
        "Rep",
        "Checks",
        "p50 latency",
        "p95 latency max",
        "turn-end p50",
        "turn-end p95",
        "STT TTFB median",
        "TTS TTFB median",
        "Interruptions",
        "Duration",
        "Mean/total cost",
        "end_call",
    )
    rows = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for call in calls:
        stt, tts = call.get("stt", "—"), call.get("tts", "—")
        grouped.setdefault((stt, tts), []).append(call)
        rows.append(
            (
                "Call",
                stt,
                tts,
                str(call.get("tts_voice") or "—"),
                call.get("scenario", "—"),
                str(call.get("rep", "—")),
                f"{call.get('checks_passed', 0)}/{call.get('checks_total', 0)}",
                _seconds(call.get("turn_latency_summary", {}).get("p50_seconds")),
                _seconds(call.get("turn_latency_summary", {}).get("p95_seconds")),
                _seconds(call.get("turn_end_latency_summary", {}).get("p50_seconds")),
                _seconds(call.get("turn_end_latency_summary", {}).get("p95_seconds")),
                _seconds(call.get("stt_ttfb_median_seconds")),
                _seconds(call.get("tts_ttfb_median_seconds")),
                _value(call.get("interruptions")),
                _seconds(call.get("call_duration_seconds")),
                _cost(call),
                str(bool(call.get("ended_via_end_call"))),
            )
        )
    for (stt, tts), combination_calls in grouped.items():
        aggregate = aggregate_calls(combination_calls)
        rows.append(
            (
                "Aggregate",
                stt,
                tts,
                ", ".join(
                    sorted(
                        {
                            str(call["tts_voice"])
                            for call in combination_calls
                            if call.get("tts_voice")
                        }
                    )
                )
                or "—",
                "all",
                "—",
                f"{aggregate['checks_passed']}/{aggregate['checks_total']}",
                _seconds(aggregate["median_p50_latency_seconds"]),
                _seconds(aggregate["max_p95_latency_seconds"]),
                "—",
                "—",
                "—",
                "—",
                "—",
                "—",
                _money(aggregate["mean_cost"]),
                "—",
            )
        )
    widths = [
        max(len(column), *(len(row[index]) for row in rows)) if rows else len(column)
        for index, column in enumerate(columns)
    ]
    lines = [
        "| "
        + " | ".join(column.ljust(widths[index]) for index, column in enumerate(columns))
        + " |",
        "| " + " | ".join("-" * width for width in widths) + " |",
    ]
    lines.extend(
        "| " + " | ".join(row[index].ljust(widths[index]) for index in range(len(columns))) + " |"
        for row in rows
    )
    lines.append("")
    lines.append(
        "Aggregate p50 is the median of per-call p50 latency; aggregate p95 is the maximum "
        "per-call p95. Costs are estimated."
    )
    return "\n".join(lines) + "\n"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _seconds(value: Any) -> str:
    return "—" if value is None else f"{float(value):.3f}s"


def _money(value: Any) -> str:
    return "—" if value is None else f"USD {float(value):.6f}"


def _value(value: Any) -> str:
    return "—" if value is None else str(value)


def _cost(call: dict[str, Any]) -> str:
    cost = call.get("estimated_cost", {})
    return _money(cost.get("total")).replace("USD ", f"{cost.get('currency', 'USD')} ")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score one scripted voice call.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--scenario", required=True)
    arguments = parser.parse_args()
    scenarios = yaml.safe_load(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
    result = score_call(arguments.run_dir, scenarios[arguments.scenario])
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
