// Hand-written types for the platform admin API (/admin/...).
// Replace with generated `Schemas[...]` once the backend admin endpoints land in openapi.json.
import type { Schemas } from "./client";

export type AdminCheckKey =
  | "answered_question"
  | "collected_required_details"
  | "language_consistent"
  | "no_unsupported_promises"
  | "ended_properly"
  | "followed_instructions";

export type AdminCheck = { key: AdminCheckKey; passed: boolean | null; note: string | null };

export type AdminObjective = {
  response_p50_s: number | null;
  response_p95_s: number | null;
  max_response_s: number | null;
  interruptions: number | null;
  turns: number | null;
  call_duration_s: number | null;
  tts_chars: number | null;
};

export type AdminScore = {
  id: string;
  conversation_id: string;
  created_at: string;
  judge_model: string;
  judge_version: string;
  overall: number;
  needs_review: boolean;
  checks: AdminCheck[];
  issues: string[];
  objective: AdminObjective | null;
};

export type AdminOverview = {
  conversations: number;
  scored: number;
  needs_review: number;
  avg_overall: number | null;
  response_p50_s: number | null;
  response_p95_s: number | null;
  by_day: { date: string; conversations: number; avg_overall: number | null; needs_review: number }[];
  by_tenant: { tenant_id: string; tenant_name: string; conversations: number; avg_overall: number | null; needs_review: number }[];
};

export type AdminConversationItem = {
  conversation_id: string;
  tenant_id: string;
  tenant_name: string;
  assistant_id: string;
  assistant_name: string;
  channel: Schemas["ConversationSummary"]["channel"];
  started_at: string;
  is_test: boolean;
  overall: number | null;
  needs_review: boolean | null;
  issues: string[];
  objective: AdminObjective | null;
};

export type AdminConversationList = { items: AdminConversationItem[]; next_cursor: string | null };

export type AdminConversationQuery = {
  needs_review?: boolean;
  tenant_id?: string;
  assistant_id?: string;
  limit?: number;
  cursor?: string;
};

export type AdminConversationDetail = {
  id: string;
  tenant_id: string;
  tenant_name: string;
  assistant_id: string;
  assistant_name: string;
  channel: Schemas["ConversationSummary"]["channel"];
  status: Schemas["ConversationSummary"]["status"];
  summary: string | null;
  started_at: string;
  ended_at: string | null;
  end_reason: string | null;
  is_test: boolean;
  messages: Schemas["MessageResponse"][];
  score: AdminScore | null;
  trace_url: string | null;
};

export type EvalCaseChecks = {
  transcript_checks: Record<string, unknown>[];
  assistant_checks: Record<string, unknown>[];
  intake_checks: Record<string, unknown>[];
};

export type AdminEvalCase = {
  id: string;
  source_conversation_id: string | null;
  name: string;
  assistant_id: string | null;
  caller_utterances: string[];
  checks: EvalCaseChecks;
  created_at: string;
  enabled: boolean;
};

export type AdminEvalResult = { eval_case_id: string; passed: boolean; failures: string[]; metrics: Record<string, unknown> };

export type AdminEvalRunSummary = {
  id: string;
  started_at: string;
  finished_at: string | null;
  git_sha: string | null;
  trigger: string;
  summary: { cases: number; passed: number; failed: number };
};

export type AdminEvalRun = AdminEvalRunSummary & { results: AdminEvalResult[] };

export type AdminEvalCaseList = { items: AdminEvalCase[] };
export type AdminEvalRunList = { items: AdminEvalRunSummary[] };
