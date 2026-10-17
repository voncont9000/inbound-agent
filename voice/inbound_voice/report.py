import argparse
import json
from pathlib import Path
from typing import Any

from inbound_voice.metrics import calculate_cost, load_price_table


def load_call(run_dir: Path) -> dict[str, Any] | None:
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        return None
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    intake_path = run_dir / "intake.json"
    tasks_path = run_dir / "tasks.json"
    metrics["intake"] = (
        json.loads(intake_path.read_text(encoding="utf-8")) if intake_path.exists() else {}
    )
    metrics["tasks"] = (
        json.loads(tasks_path.read_text(encoding="utf-8")) if tasks_path.exists() else []
    )
    return metrics


def collect_report(runs_dir: str | Path, prices_path: str | Path) -> list[dict[str, Any]]:
    prices = load_price_table(prices_path)
    calls: list[dict[str, Any]] = []
    runs_path = Path(runs_dir)
    if not runs_path.exists():
        return calls
    for call_dir in sorted(runs_path.iterdir()):
        if not call_dir.is_dir():
            continue
        metrics = load_call(call_dir)
        if metrics is not None:
            metrics["call_id"] = metrics.get("call_id", call_dir.name)
            metrics["cost"] = calculate_cost(metrics, prices)
            calls.append(metrics)
    return calls


def print_report(calls: list[dict[str, Any]]) -> None:
    headings = (
        "CALL",
        "STT",
        "TTS",
        "LLM MODEL",
        "DURATION",
        "TURNS",
        "VAD P50",
        "VAD P95",
        "VAD MAX",
        "TURN END P50",
        "TURN END P95",
        "TURN END MAX",
        "COST",
    )
    rows = []
    for call in calls:
        summary = call.get("turn_latency_summary", {})
        turn_end_summary = call.get("turn_end_latency_summary", {})
        providers = call.get("providers") or {}
        cost = call["cost"]
        rows.append(
            (
                str(call["call_id"]),
                str(providers.get("stt") or "deepgram"),
                str(providers.get("tts") or "deepgram"),
                str(providers.get("llm_model") or "google/gemini-2.5-flash"),
                _seconds(call.get("call_duration_seconds")),
                str(summary.get("turn_count", len(call.get("turns", [])))),
                _seconds(summary.get("p50_seconds")),
                _seconds(summary.get("p95_seconds")),
                _seconds(summary.get("max_seconds")),
                _seconds(turn_end_summary.get("p50_seconds")),
                _seconds(turn_end_summary.get("p95_seconds")),
                _seconds(turn_end_summary.get("max_seconds")),
                f"{cost['currency']} {cost['total']:.6f}*"
                if cost["unknown_components"]
                else f"{cost['currency']} {cost['total']:.6f}",
            )
        )
    widths = [
        max(len(headings[index]), *(len(row[index]) for row in rows))
        if rows
        else len(headings[index])
        for index in range(len(headings))
    ]
    print("  ".join(headings[index].ljust(widths[index]) for index in range(len(headings))))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(row[index].ljust(widths[index]) for index in range(len(headings))))
    if any(call["cost"]["unknown_components"] for call in calls):
        print("* Partial estimated cost; unknown price or usage components are not included.")


def _seconds(value: Any) -> str:
    return "—" if value is None else f"{float(value):.3f}s"


def main() -> None:
    parser = argparse.ArgumentParser(description="Report voice-call latency and estimated costs.")
    parser.add_argument("runs_dir", type=Path)
    parser.add_argument(
        "--prices",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config" / "prices.yaml",
    )
    arguments = parser.parse_args()
    print_report(collect_report(arguments.runs_dir, arguments.prices))


if __name__ == "__main__":
    main()
