from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any

import yaml

from inbound_voice.metrics import calculate_cost, load_price_table, percentile

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
    assistant_text = " ".join(
        str(row.get("text", "")) for row in transcript if row.get("role") == "assistant"
    )
    intake = _read_json(directory / "intake.json", {})
    tasks = _read_json(directory / "tasks.json", [])
    metrics = _read_json(directory / "metrics.json", {})
    caller_timeline = _read_json(directory / "caller_timeline.json", {})

    transcript_checks = {
        name: {
            "pattern": pattern,
            "passed": re.search(pattern, user_text, re.IGNORECASE) is not None,
        }
        for name, pattern in scenario.get("transcript_checks", {}).items()
    }
    assistant_checks = {
        name: {
            "pattern": pattern,
            "passed": re.search(pattern, assistant_text, re.IGNORECASE) is not None,
        }
        for name, pattern in scenario.get("assistant_checks", {}).items()
    }
    assistant_max_matches = {}
    for name, spec in scenario.get("assistant_max_matches", {}).items():
        pattern = spec["pattern"]
        maximum = int(spec["max"])
        count = len(re.findall(pattern, assistant_text, re.IGNORECASE))
        assistant_max_matches[name] = {
            "pattern": pattern,
            "max": maximum,
            "actual": count,
            "passed": count <= maximum,
        }
    assistant_not_matches = {}
    not_match_specs = scenario.get("assistant_not_matches", {})
    if isinstance(not_match_specs, str):
        not_match_specs = {"unexpected_match": not_match_specs}
    for name, spec in not_match_specs.items():
        pattern = spec if isinstance(spec, str) else spec["pattern"]
        count = len(re.findall(pattern, assistant_text, re.IGNORECASE))
        assistant_not_matches[name] = {
            "pattern": pattern,
            "actual": count,
            "passed": count == 0,
        }
    fields = intake.get("fields", {}) if isinstance(intake, dict) else {}
    patterns = scenario.get("intake_checks", {})
    intake_checks = _intake_checks(fields, patterns)
    live_intake = _read_json(directory / "intake.live.json", {})
    post_call_intake = _read_json(directory / "intake.post_call.json", {})
    live_intake_checks = _intake_checks(live_intake.get("fields", {}), patterns)
    post_call_intake_checks = _intake_checks(post_call_intake.get("fields", {}), patterns)

    end_call_task = next(
        (task for task in tasks if isinstance(task, dict) and task.get("type") == "end_call"),
        None,
    )
    end_call_source = end_call_task.get("source") or "tool" if end_call_task is not None else None
    task_checks = {}
    task_types = {task.get("type") for task in tasks if isinstance(task, dict)}
    for name, expected in scenario.get("task_checks", {}).items():
        if name == "number_hold_resumed":
            actual = any(
                hold.get("resumed")
                for hold in caller_timeline.get("number_holds", [])
                if isinstance(hold, dict)
            )
        else:
            actual = "urgent" in task_types if name == "urgent" else name in task_types
        task_checks[name] = {
            "expected": expected,
            "actual": actual,
            "passed": actual is expected,
        }
    if scenario.get("no_agent_speech_between_segments"):
        gaps = [
            (segments[index]["end_seconds"], segments[index + 1]["start_seconds"])
            for utterance in caller_timeline.get("utterances", [])
            for segments in [utterance.get("segments", [])]
            if len(segments) >= 2
            for index in range(len(segments) - 1)
            if segments[index].get("end_seconds") is not None
            and segments[index + 1].get("start_seconds") is not None
        ]
        interrupted = any(
            start < float(speech.get("start_seconds", 0)) < end
            for start, end in gaps
            for speech in caller_timeline.get("agent_speech", [])
            if speech.get("start_seconds") is not None
        )
        task_checks["no_agent_speech_between_segments"] = {
            "expected": True,
            "actual": not interrupted and bool(gaps),
            "passed": not interrupted and bool(gaps),
        }
    if "expected_end_call_source" in scenario:
        expected = scenario["expected_end_call_source"]
        task_checks["end_call_source"] = {
            "expected": expected,
            "actual": end_call_source,
            "passed": end_call_source == expected,
        }
    if "expected_server_disconnect" in scenario:
        expected = scenario["expected_server_disconnect"]
        actual = bool(caller_timeline.get("server_disconnect_detected"))
        task_checks["server_disconnect_detected"] = {
            "expected": expected,
            "actual": actual,
            "passed": actual is expected,
        }

    knowledge_gaps = _read_json(directory / "knowledge_gaps.json", {})
    unanswered = knowledge_gaps.get("unanswered_questions", [])
    gap_text = " ".join(str(item.get("question", "")) for item in unanswered)
    all_gap_text = json.dumps(knowledge_gaps, ensure_ascii=False)
    knowledge_gap_checks = {}
    for name, spec in scenario.get("knowledge_gap_checks", {}).items():
        pattern = spec["pattern"]
        expected_present = spec.get("present", True)
        text = all_gap_text if spec.get("scope") == "all" else gap_text
        actual_present = re.search(pattern, text, re.IGNORECASE) is not None
        knowledge_gap_checks[name] = {
            "pattern": pattern,
            "expected_present": expected_present,
            "actual_present": actual_present,
            "passed": actual_present is expected_present,
        }

    checks = [
        *transcript_checks.values(),
        *assistant_checks.values(),
        *assistant_max_matches.values(),
        *assistant_not_matches.values(),
        *intake_checks.values(),
        *task_checks.values(),
        *knowledge_gap_checks.values(),
    ]
    price_table = prices if prices is not None else load_price_table(PRICES_PATH)
    cost = calculate_cost(metrics, price_table)
    service_metrics = metrics.get("service_metrics", [])
    provider_metadata = metrics.get("providers") or {}
    stt_ttfb = _ttfb_median(service_metrics, "stt")
    tts_ttfb = _ttfb_median(service_metrics, "tts")
    llm_ttfb = _ttfb_median(service_metrics, "llm")
    task_sources = {}
    for task in tasks:
        if isinstance(task, dict):
            source = task.get("source", "legacy")
            task_sources[source] = task_sources.get(source, 0) + 1
    number_holds = metrics.get("number_holds") or caller_timeline.get("number_holds", [])
    number_hold_resumed_count = sum(
        bool(item.get("resumed")) for item in number_holds if isinstance(item, dict)
    )

    return {
        "call_id": metrics.get("call_id", directory.name),
        "run_dir": str(directory),
        "transcript_checks": transcript_checks,
        "assistant_checks": assistant_checks,
        "assistant_max_matches": assistant_max_matches,
        "assistant_not_matches": assistant_not_matches,
        "knowledge_gap_checks": knowledge_gap_checks,
        "intake_checks": intake_checks,
        "live_intake_checks": live_intake_checks,
        "post_call_intake_checks": post_call_intake_checks,
        "needs_review": intake.get("needs_review", []) if isinstance(intake, dict) else [],
        "task_checks": task_checks,
        "checks_passed": sum(bool(check["passed"]) for check in checks),
        "checks_total": len(checks),
        "turn_latency_summary": metrics.get("turn_latency_summary", {}),
        "turn_end_latency_summary": metrics.get("turn_end_latency_summary", {}),
        "number_holds": number_holds,
        "number_hold_resumed_count": number_hold_resumed_count,
        "number_hold_not_resumed_count": len(number_holds) - number_hold_resumed_count,
        "turn_latencies_seconds": [
            float(turn["seconds"])
            for turn in metrics.get("turns", [])
            if turn.get("seconds") is not None
        ],
        "latency_breakdown_medians": _latency_breakdown_medians(
            metrics.get("turns", []), service_metrics
        ),
        "stt_ttfb_median_seconds": stt_ttfb,
        "tts_ttfb_median_seconds": tts_ttfb,
        "llm_ttfb_median_seconds": llm_ttfb,
        "interruptions": metrics.get("interruption_count", metrics.get("interruptions")),
        "call_duration_seconds": metrics.get("call_duration_seconds"),
        "estimated_cost": cost,
        "ended_via_end_call": "end_call" in task_types,
        "end_call_source": end_call_source,
        "server_disconnect_detected": bool(caller_timeline.get("server_disconnect_detected")),
        "seconds_from_last_agent_speech_end_to_disconnect": caller_timeline.get(
            "seconds_from_last_agent_speech_end_to_disconnect"
        ),
        "task_sources": task_sources,
        "tts_voice": provider_metadata.get("tts_voice"),
        "llm": provider_metadata.get("llm"),
        "llm_model": provider_metadata.get("llm_model"),
    }


def _intake_checks(fields: dict[str, Any], patterns: dict[str, str]) -> dict[str, Any]:
    checks = {}
    for field, pattern in patterns.items():
        entry = fields.get(field)
        value = entry.get("value") if isinstance(entry, dict) else entry
        checks[field] = {
            "pattern": pattern,
            "value": value,
            "passed": (
                value is not None and re.search(pattern, str(value), re.IGNORECASE) is not None
            ),
        }
    return checks


def _ttfb_median(service_metrics: list[dict[str, Any]], kind: str) -> float | None:
    values = []
    service_marker = f"{kind}service"
    for metric in service_metrics:
        processor = str(metric.get("processor", "")).lower()
        if service_marker not in processor:
            continue
        if metric.get("model", "not-recorded") is None:
            continue
        if kind == "llm" and metric.get("kind") != "ttfb":
            continue
        value = metric.get("ttfb_seconds")
        if value is None and metric.get("kind") == "ttfb":
            value = metric.get("seconds")
        if value is not None:
            values.append(float(value))
    return statistics.median(values) if values else None


def _latency_breakdown_medians(
    turns: list[dict[str, Any]], service_metrics: list[dict[str, Any]]
) -> dict[str, float | None]:
    stages: dict[str, list[float]] = {
        "caller_stop_to_turn_end_seconds": [],
        "turn_end_to_llm_first_token_seconds": [],
        "llm_first_token_to_tts_first_audio_seconds": [],
    }
    llm_events = sorted(
        float(metric["monotonic_timestamp"])
        for metric in service_metrics
        if "llmservice" in str(metric.get("processor", "")).lower()
        and metric.get("kind") == "ttfb"
        and metric.get("model") is not None
        and metric.get("monotonic_timestamp") is not None
    )
    tts_events = sorted(
        float(metric["monotonic_timestamp"])
        for metric in service_metrics
        if "ttsservice" in str(metric.get("processor", "")).lower()
        and metric.get("kind") == "ttfa"
        and metric.get("model") is not None
        and metric.get("monotonic_timestamp") is not None
    )
    for turn in turns:
        turn_end = turn.get("user_stopped_speaking_at")
        audio_at = turn.get("first_bot_audio_at")
        caller_stop = turn.get("vad_user_stopped_speaking_at")
        if turn_end is None or audio_at is None:
            continue
        if caller_stop is not None:
            stages["caller_stop_to_turn_end_seconds"].append(
                max(0.0, float(turn_end) - float(caller_stop))
            )
        llm_at = next((event for event in llm_events if turn_end <= event <= audio_at), None)
        if llm_at is None:
            continue
        stages["turn_end_to_llm_first_token_seconds"].append(float(llm_at - turn_end))
        tts_at = next((event for event in tts_events if llm_at <= event <= audio_at + 0.25), None)
        if tts_at is not None:
            stages["llm_first_token_to_tts_first_audio_seconds"].append(float(tts_at - llm_at))
    return {name: statistics.median(values) if values else None for name, values in stages.items()}


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
    all_turn_latencies = [
        float(value) for call in calls for value in call.get("turn_latencies_seconds", [])
    ]
    max_latencies = [
        call["turn_latency_summary"]["max_seconds"]
        for call in calls
        if call.get("turn_latency_summary", {}).get("max_seconds") is not None
    ]
    max_turn_end_latencies = [
        call["turn_end_latency_summary"]["max_seconds"]
        for call in calls
        if call.get("turn_end_latency_summary", {}).get("max_seconds") is not None
    ]
    disconnect_gaps = [
        float(call["seconds_from_last_agent_speech_end_to_disconnect"])
        for call in calls
        if call.get("seconds_from_last_agent_speech_end_to_disconnect") is not None
    ]
    disconnect_values = [
        call["server_disconnect_detected"]
        for call in calls
        if call.get("server_disconnect_detected") is not None
    ]
    return {
        "calls": len(calls),
        "median_p50_latency_seconds": statistics.median(latency_p50) if latency_p50 else None,
        "max_p95_latency_seconds": max(latency_p95) if latency_p95 else None,
        "all_turns_p95_latency_seconds": percentile(all_turn_latencies, 95),
        "max_latency_seconds": max(max_latencies) if max_latencies else None,
        "max_turn_end_latency_seconds": (
            max(max_turn_end_latencies) if max_turn_end_latencies else None
        ),
        "server_disconnect_count": sum(bool(value) for value in disconnect_values),
        "server_disconnect_total": len(disconnect_values),
        "median_disconnect_gap_seconds": (
            statistics.median(disconnect_gaps) if disconnect_gaps else None
        ),
        "checks_passed": sum(call.get("checks_passed", 0) for call in calls),
        "checks_total": sum(call.get("checks_total", 0) for call in calls),
        "mean_cost": statistics.fmean(costs) if costs else None,
    }


def render_summary(calls: list[dict[str, Any]]) -> str:
    columns = (
        "Kind",
        "STT",
        "TTS",
        "LLM",
        "Voice",
        "Scenario",
        "Transport",
        "Report posted",
        "Rep",
        "Smart turn stop",
        "Checks",
        "Intake live / post / merged",
        "Review",
        "Task sources",
        "p50 latency",
        "p95 latency max",
        "max latency",
        "turn-end p50",
        "turn-end p95",
        "turn-end max",
        "all-turn p95",
        "Caller stop→turn-end median",
        "Turn-end→LLM median",
        "LLM→TTS first audio median",
        "LLM TTFB median",
        "STT TTFB median",
        "TTS TTFB median",
        "Interruptions",
        "Duration",
        "Mean/total cost",
        "End mode",
        "Server disconnect",
        "Speech-end→disconnect",
    )
    rows = []
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for call in calls:
        stt, tts = call.get("stt", "—"), call.get("tts", "—")
        llm = str(call.get("llm") or "—")
        llm_model = str(call.get("llm_model") or "—")
        grouped.setdefault((stt, tts, llm, llm_model), []).append(call)
        rows.append(
            (
                "Call",
                stt,
                tts,
                _llm_label(llm, llm_model),
                str(call.get("tts_voice") or "—"),
                call.get("scenario", "—"),
                str(call.get("transport") or "webrtc"),
                str(call.get("report_posted")) if call.get("report_posted") is not None else "—",
                str(call.get("rep", "—")),
                _seconds(call.get("smart_turn_stop_secs")),
                f"{call.get('checks_passed', 0)}/{call.get('checks_total', 0)}",
                " · ".join(
                    _check_count(call.get(key, {}))
                    for key in (
                        "live_intake_checks",
                        "post_call_intake_checks",
                        "intake_checks",
                    )
                ),
                str(len(call.get("needs_review", []))),
                _task_sources(call.get("task_sources", {})),
                _seconds(call.get("turn_latency_summary", {}).get("p50_seconds")),
                _seconds(call.get("turn_latency_summary", {}).get("p95_seconds")),
                _seconds(call.get("turn_latency_summary", {}).get("max_seconds")),
                _seconds(call.get("turn_end_latency_summary", {}).get("p50_seconds")),
                _seconds(call.get("turn_end_latency_summary", {}).get("p95_seconds")),
                _seconds(call.get("turn_end_latency_summary", {}).get("max_seconds")),
                "—",
                _seconds(
                    call.get("latency_breakdown_medians", {}).get("caller_stop_to_turn_end_seconds")
                ),
                _seconds(
                    call.get("latency_breakdown_medians", {}).get(
                        "turn_end_to_llm_first_token_seconds"
                    )
                ),
                _seconds(
                    call.get("latency_breakdown_medians", {}).get(
                        "llm_first_token_to_tts_first_audio_seconds"
                    )
                ),
                _seconds(call.get("llm_ttfb_median_seconds")),
                _seconds(call.get("stt_ttfb_median_seconds")),
                _seconds(call.get("tts_ttfb_median_seconds")),
                _value(call.get("interruptions")),
                _seconds(call.get("call_duration_seconds")),
                _cost(call),
                _end_mode(call),
                _value(call.get("server_disconnect_detected")),
                _seconds(call.get("seconds_from_last_agent_speech_end_to_disconnect")),
            )
        )
    for (stt, tts, llm, llm_model), combination_calls in grouped.items():
        aggregate = aggregate_calls(combination_calls)
        rows.append(
            (
                "Aggregate",
                stt,
                tts,
                _llm_label(llm, llm_model),
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
                "—",
                "—",
                "—",
                f"{aggregate['checks_passed']}/{aggregate['checks_total']}",
                " · ".join(
                    _aggregate_check_count(combination_calls, key)
                    for key in (
                        "live_intake_checks",
                        "post_call_intake_checks",
                        "intake_checks",
                    )
                ),
                str(sum(len(call.get("needs_review", [])) for call in combination_calls)),
                _aggregate_task_sources(combination_calls),
                _seconds(aggregate["median_p50_latency_seconds"]),
                _seconds(aggregate["max_p95_latency_seconds"]),
                _seconds(aggregate["max_latency_seconds"]),
                "—",
                "—",
                _seconds(aggregate["max_turn_end_latency_seconds"]),
                _seconds(aggregate["all_turns_p95_latency_seconds"]),
                "—",
                "—",
                "—",
                "—",
                "—",
                "—",
                "—",
                "—",
                _money(aggregate["mean_cost"]),
                "—",
                (
                    f"{aggregate['server_disconnect_count']}/{aggregate['server_disconnect_total']}"
                    if aggregate["server_disconnect_total"]
                    else "—"
                ),
                _seconds(aggregate["median_disconnect_gap_seconds"]),
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
        "Aggregate p50 is the median of per-call p50; per-call p95 is interpolated within "
        "each call; all-turn p95 is interpolated across every measured turn. Disconnect "
        "shows detected/total calls, and the gap is the median across detected calls. "
        "Costs are estimated."
    )
    return "\n".join(lines) + "\n"


def _check_count(checks: dict[str, Any]) -> str:
    return f"{sum(bool(check.get('passed')) for check in checks.values())}/{len(checks)}"


def _aggregate_check_count(calls: list[dict[str, Any]], key: str) -> str:
    passed = sum(
        sum(bool(check.get("passed")) for check in call.get(key, {}).values()) for call in calls
    )
    total = sum(len(call.get(key, {})) for call in calls)
    return f"{passed}/{total}"


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


def _llm_label(provider: Any, model: Any) -> str:
    if provider == "—" and model == "—":
        return "—"
    if provider == "—":
        return str(model)
    if model == "—":
        return str(provider)
    return f"{provider} ({model})"


def _task_sources(sources: dict[str, int]) -> str:
    return " · ".join(f"{source}:{count}" for source, count in sorted(sources.items())) or "—"


def _aggregate_task_sources(calls: list[dict[str, Any]]) -> str:
    totals: dict[str, int] = {}
    for call in calls:
        for source, count in call.get("task_sources", {}).items():
            totals[source] = totals.get(source, 0) + count
    return _task_sources(totals)


def _end_mode(call: dict[str, Any]) -> str:
    source = call.get("end_call_source")
    if source in {"fallback", "goodbye_detected", "idle"}:
        return source
    if source in {"live", "tool"} or (source is None and call.get("ended_via_end_call")):
        return "tool"
    return "none"


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
