import type { Schemas } from "../api/client";
import type {
  AdminCheck,
  AdminConversationDetail,
  AdminConversationItem,
  AdminEvalCase,
  AdminEvalRun,
  AdminEvalRunSummary,
  AdminObjective,
  AdminOverview,
  AdminScore,
} from "../api/admin";

export const adminTenants = [
  { tenant_id: "71000000-0000-4000-8000-000000000001", tenant_name: "Northstar Services" },
  { tenant_id: "71000000-0000-4000-8000-000000000002", tenant_name: "Fieldwork Studio" },
  { tenant_id: "71000000-0000-4000-8000-000000000003", tenant_name: "Common Ground" },
];

export const adminAssistants = [
  { assistant_id: "72000000-0000-4000-8000-000000000001", assistant_name: "Avery" },
  { assistant_id: "72000000-0000-4000-8000-000000000002", assistant_name: "Morgan" },
  { assistant_id: "72000000-0000-4000-8000-000000000003", assistant_name: "Taylor" },
  { assistant_id: "72000000-0000-4000-8000-000000000004", assistant_name: "Jordan" },
];

const fixedId = (index: number) => `73000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
const isoDaysAgo = (days: number, hour = 10) => {
  const date = new Date();
  date.setDate(date.getDate() - days);
  date.setHours(hour, (days * 7) % 60, 0, 0);
  return date.toISOString();
};
const addSeconds = (timestamp: string, seconds: number) => new Date(Date.parse(timestamp) + seconds * 1000).toISOString();

const keys: AdminCheck["key"][] = [
  "answered_question",
  "collected_required_details",
  "language_consistent",
  "no_unsupported_promises",
  "ended_properly",
  "followed_instructions",
];

function checksFor(index: number): AdminCheck[] {
  const failedKey = index === 0 ? keys[1] : index % 3 === 0 ? keys[(index / 3) % keys.length] : null;
  return keys.map((key) => ({
    key,
    passed: key === failedKey ? false : index === 5 && key === "ended_properly" ? null : true,
    note: key === failedKey
      ? key === "collected_required_details" ? "Did not confirm the callback number." : "The response did not follow the requested steps."
      : null,
  }));
}

function objectiveFor(channel: Schemas["ConversationSummary"]["channel"], index: number): AdminObjective | null {
  if (channel !== "phone" && channel !== "web") return null;
  return {
    response_p50_s: Number((1.1 + index * 0.17).toFixed(1)),
    response_p95_s: Number((2.4 + index * 0.31).toFixed(1)),
    max_response_s: Number((3.2 + index * 0.4).toFixed(1)),
    interruptions: index % 4,
    turns: 4 + index % 7,
    call_duration_s: 82 + index * 13,
    tts_chars: 260 + index * 37,
  };
}

const channelList: Schemas["ConversationSummary"]["channel"][] = [
  "phone", "whatsapp", "gmail", "web", "phone", "gmail",
  "whatsapp", "phone", "web", "gmail", "phone", "whatsapp",
];
const issueByIndex: Record<number, string[]> = {
  0: ["Did not confirm the callback number"],
  3: ["Promised a delivery time without checking availability"],
  6: ["Did not answer the caller's pricing question"],
  9: ["Switched language midway through the conversation"],
};

export const adminConversationDetails: AdminConversationDetail[] = channelList.map((channel, index) => {
  const tenant = adminTenants[index % adminTenants.length];
  const assistant = adminAssistants[index % adminAssistants.length];
  const startedAt = isoDaysAgo(index, 8 + index % 9);
  const objective = objectiveFor(channel, index);
  const callDurationSeconds = objective?.call_duration_s ?? 39 + index * 6;
  const scored = index !== 10;
  const needsReview = scored && (index % 3 === 0 || index === 7 || index === 11);
  const checks = checksFor(index);
  const issues = issueByIndex[index] ?? (needsReview ? ["The response needs a closer review"] : []);
  const score: AdminScore | null = scored ? {
    id: `74000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
    conversation_id: fixedId(index + 1),
    created_at: isoDaysAgo(index, 11),
    judge_model: "gemini-3.5-flash-lite",
    judge_version: "v1",
    overall: needsReview ? (index === 3 ? 2 : 3) : index % 2 ? 4 : 5,
    needs_review: needsReview,
    checks,
    issues,
    objective,
  } : null;
  const messages: Schemas["MessageResponse"][] = [
    { id: `message-${index}-1`, role: "caller", text: "I have a question about arranging a callback.", ts: startedAt, meta: {} },
    { id: `message-${index}-2`, role: "assistant", text: "I can help with that. What is the best number to reach you on?", ts: addSeconds(startedAt, 4), meta: {} },
    { id: `message-${index}-3`, role: "caller", text: "Please use my usual number.", ts: addSeconds(startedAt, 9), meta: {} },
    { id: `message-${index}-4`, role: "assistant", text: "Thank you. I will pass your request to the team.", ts: addSeconds(startedAt, 16), meta: {} },
  ];
  return {
    id: fixedId(index + 1),
    tenant_id: tenant.tenant_id,
    tenant_name: tenant.tenant_name,
    assistant_id: assistant.assistant_id,
    assistant_name: assistant.assistant_name,
    channel,
    status: "completed",
    summary: "Caller asked for help arranging a callback.",
    started_at: startedAt,
    ended_at: addSeconds(startedAt, callDurationSeconds),
    end_reason: "completed",
    is_test: index === 4,
    messages,
    score,
    trace_url: index === 2 ? null : `https://trace.example.test/conversation/${fixedId(index + 1)}`,
  };
});

export function adminItem(detail: AdminConversationDetail): AdminConversationItem {
  return {
    conversation_id: detail.id,
    tenant_id: detail.tenant_id,
    tenant_name: detail.tenant_name,
    assistant_id: detail.assistant_id,
    assistant_name: detail.assistant_name,
    channel: detail.channel,
    started_at: detail.started_at,
    is_test: detail.is_test,
    overall: detail.score?.overall ?? null,
    needs_review: detail.score?.needs_review ?? null,
    issues: detail.score?.issues ?? [],
    objective: detail.score?.objective ?? null,
  };
}

export const adminEvalCases: AdminEvalCase[] = [
  {
    id: "75000000-0000-4000-8000-000000000001",
    source_conversation_id: adminConversationDetails[0].id,
    name: "Callback details are confirmed",
    assistant_id: adminConversationDetails[0].assistant_id,
    caller_utterances: ["I need someone to call me back.", "My number is 020 0000 0000."],
    checks: { transcript_checks: [{ key: "callback_number_collected" }], assistant_checks: [{ key: "confirms_number" }], intake_checks: [] },
    created_at: isoDaysAgo(3),
    enabled: true,
  },
  {
    id: "75000000-0000-4000-8000-000000000002",
    source_conversation_id: adminConversationDetails[3].id,
    name: "Availability is checked before a promise",
    assistant_id: adminConversationDetails[3].assistant_id,
    caller_utterances: ["Can you deliver this afternoon?"],
    checks: { transcript_checks: [], assistant_checks: [{ key: "no_unsupported_promises" }], intake_checks: [{ key: "availability_checked" }] },
    created_at: isoDaysAgo(8),
    enabled: false,
  },
];

const evalRunSummaries: AdminEvalRunSummary[] = [
  { id: "76000000-0000-4000-8000-000000000001", started_at: isoDaysAgo(0, 15), finished_at: isoDaysAgo(0, 15), git_sha: "a1b2c3d4", trigger: "manual", summary: { cases: 2, passed: 1, failed: 1 } },
  { id: "76000000-0000-4000-8000-000000000002", started_at: isoDaysAgo(2, 9), finished_at: isoDaysAgo(2, 9), git_sha: "e5f6a7b8", trigger: "commit", summary: { cases: 2, passed: 2, failed: 0 } },
  { id: "76000000-0000-4000-8000-000000000003", started_at: isoDaysAgo(6, 12), finished_at: isoDaysAgo(6, 12), git_sha: null, trigger: "scheduled", summary: { cases: 2, passed: 1, failed: 1 } },
];

export let mockAdminEvalCases = [...adminEvalCases];
export let mockAdminEvalRuns: AdminEvalRun[] = [
  {
    ...evalRunSummaries[0],
    results: [
      { eval_case_id: adminEvalCases[0].id, passed: false, failures: ["Expected the assistant to read the callback number back.", "Required intake field phone was missing."], metrics: {} },
      { eval_case_id: adminEvalCases[1].id, passed: true, failures: [], metrics: {} },
    ],
  },
  {
    ...evalRunSummaries[1],
    results: evalRunSummaries[1].summary.cases === 2 ? adminEvalCases.map(({ id }) => ({ eval_case_id: id, passed: true, failures: [], metrics: {} })) : [],
  },
  {
    ...evalRunSummaries[2],
    results: [
      { eval_case_id: adminEvalCases[0].id, passed: true, failures: [], metrics: {} },
      { eval_case_id: adminEvalCases[1].id, passed: false, failures: ["Assistant promised a delivery time without checking availability."], metrics: {} },
    ],
  },
];

export const resetAdminMocks = () => {
  mockAdminEvalCases = [...adminEvalCases];
  mockAdminEvalRuns = mockAdminEvalRuns.map((run, index) => ({ ...run, ...evalRunSummaries[index] }));
};

export function adminEvalRunSummaries(): AdminEvalRunSummary[] {
  return mockAdminEvalRuns.map((run) => ({
    id: run.id,
    started_at: run.started_at,
    finished_at: run.finished_at,
    git_sha: run.git_sha,
    trigger: run.trigger,
    summary: run.summary,
  }));
}

export function adminOverview(days: number): AdminOverview {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - days + 1);
  const selected = adminConversationDetails.filter(({ started_at }) => new Date(started_at) >= start);
  const scored = selected.filter(({ score }) => score !== null);
  const reviewed = scored.filter(({ score }) => score?.needs_review);
  const responseValues = scored.flatMap(({ score }) => score?.objective?.response_p50_s ?? []);
  const p50 = responseValues.length
    ? Number((responseValues.reduce((sum, value) => sum + value, 0) / responseValues.length).toFixed(1))
    : null;
  const daysData = Array.from({ length: days }, (_, index) => {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    const dateString = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    const rows = selected.filter(({ started_at }) => started_at.slice(0, 10) === dateString);
    const scores = rows.flatMap(({ score }) => score ? [score.overall] : []);
    return {
      date: dateString,
      conversations: rows.length,
      avg_overall: scores.length ? Number((scores.reduce((sum, value) => sum + value, 0) / scores.length).toFixed(1)) : null,
      needs_review: rows.filter(({ score }) => score?.needs_review).length,
    };
  });
  return {
    conversations: selected.length,
    scored: scored.length,
    needs_review: reviewed.length,
    avg_overall: scored.length ? Number((scored.reduce((sum, { score }) => sum + (score?.overall ?? 0), 0) / scored.length).toFixed(1)) : null,
    response_p50_s: p50,
    response_p95_s: responseValues.length ? Number((Math.max(...responseValues) * 1.8).toFixed(1)) : null,
    by_day: daysData,
    by_tenant: adminTenants.map(({ tenant_id, tenant_name }) => {
      const rows = selected.filter((item) => item.tenant_id === tenant_id);
      const scores = rows.flatMap(({ score }) => score ? [score.overall] : []);
      return {
        tenant_id,
        tenant_name,
        conversations: rows.length,
        avg_overall: scores.length ? Number((scores.reduce((sum, value) => sum + value, 0) / scores.length).toFixed(1)) : null,
        needs_review: rows.filter(({ score }) => score?.needs_review).length,
      };
    }),
  };
}
