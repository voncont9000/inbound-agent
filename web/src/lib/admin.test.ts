import { describe, expect, it } from "vitest";
import { countCaseChecks, readObjective, readRunResults, readRunSummary } from "./admin";

describe("admin response readers", () => {
  it("reads numeric objective fields and ignores unknown or non-numeric values", () => {
    expect(readObjective({
      response_p50_s: 1.2,
      response_p95_s: "2.4",
      max_response_s: 3.1,
      interruptions: 0,
      turns: 4,
      call_duration_s: null,
      tts_chars: 260,
      extra: 99,
    })).toEqual({
      response_p50_s: 1.2,
      response_p95_s: null,
      max_response_s: 3.1,
      interruptions: 0,
      turns: 4,
      call_duration_s: null,
      tts_chars: 260,
    });
    expect(readObjective(null)).toBeNull();
  });

  it("reads run summary counts and defaults absent values to zero", () => {
    expect(readRunSummary({ cases: 3, passed: 2, failed: 1, extra: "ignored" })).toEqual({
      cases: 3,
      passed: 2,
      failed: 1,
    });
    expect(readRunSummary({})).toEqual({ cases: 0, passed: 0, failed: 0 });
  });

  it("reads result fields defensively", () => {
    expect(readRunResults([
      { eval_case_id: "case-1", passed: true, failures: [], metrics: {} },
      { eval_case_id: null, passed: false, failures: ["missing field", 3], metrics: {} },
    ])).toEqual([
      { eval_case_id: "case-1", passed: true, failures: [] },
      { eval_case_id: "", passed: false, failures: ["missing field"] },
    ]);
  });

  it("counts keys across the three case check dictionaries", () => {
    expect(countCaseChecks({
      transcript_checks: { callback: true, language: true },
      assistant_checks: { readback: true },
      intake_checks: { phone: true, name: false },
    })).toBe(5);
    expect(countCaseChecks({ transcript_checks: {}, assistant_checks: null, intake_checks: [] })).toBe(0);
  });
});
