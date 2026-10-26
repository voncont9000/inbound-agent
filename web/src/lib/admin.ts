import type { EvalCaseResponse, EvalRunResponse, EvalRunSummary } from "../api/adminApi";

export function scoreTone(overall: number | null | undefined): "neutral" | "success" | "warning" | "danger" {
  if (overall == null) return "neutral";
  if (overall >= 4) return "success";
  if (overall === 3) return "warning";
  return "danger";
}

export type AdminObjective = {
  response_p50_s: number | null;
  response_p95_s: number | null;
  max_response_s: number | null;
  interruptions: number | null;
  turns: number | null;
  call_duration_s: number | null;
  tts_chars: number | null;
};

function readNumber(object: Record<string, unknown> | null, key: string): number | null {
  const value = object?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function readObjective(objective: Record<string, unknown> | null): AdminObjective | null {
  if (!objective) return null;
  return {
    response_p50_s: readNumber(objective, "response_p50_s"),
    response_p95_s: readNumber(objective, "response_p95_s"),
    max_response_s: readNumber(objective, "max_response_s"),
    interruptions: readNumber(objective, "interruptions"),
    turns: readNumber(objective, "turns"),
    call_duration_s: readNumber(objective, "call_duration_s"),
    tts_chars: readNumber(objective, "tts_chars"),
  };
}

export type AdminRunSummary = { cases: number; passed: number; failed: number };

function readCount(summary: Record<string, unknown>, key: string): number {
  const value = summary[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function readRunSummary(summary: EvalRunSummary["summary"] | EvalRunResponse["summary"]): AdminRunSummary {
  return {
    cases: readCount(summary, "cases"),
    passed: readCount(summary, "passed"),
    failed: readCount(summary, "failed"),
  };
}

export type AdminRunResult = { eval_case_id: string; passed: boolean; failures: string[] };

export function readRunResults(results: EvalRunResponse["results"]): AdminRunResult[] {
  return results.map((result) => ({
    eval_case_id: typeof result.eval_case_id === "string" ? result.eval_case_id : "",
    passed: result.passed === true,
    failures: Array.isArray(result.failures)
      ? result.failures.filter((failure): failure is string => typeof failure === "string")
      : [],
  }));
}

export function countCaseChecks(checks: EvalCaseResponse["checks"]): number {
  return ["transcript_checks", "assistant_checks", "intake_checks"].reduce((total, section) => {
    const value = checks[section];
    return total + (value && typeof value === "object" && !Array.isArray(value) ? Object.keys(value).length : 0);
  }, 0);
}
