import { api, unwrap } from "./client";
import type { components, operations } from "./schema";

type AdminSchemas = components["schemas"];

export type AdminOverview = AdminSchemas["AdminOverview"];
export type AdminConversationItem = AdminSchemas["AdminConversationItem"];
export type AdminConversationListResponse = AdminSchemas["AdminConversationListResponse"];
export type AdminConversationDetail = AdminSchemas["AdminConversationDetail"];
export type ConversationScoreResponse = AdminSchemas["ConversationScoreResponse"];
export type ConversationScoreCheck = AdminSchemas["ConversationScoreCheck"];
export type EvalCaseResponse = AdminSchemas["EvalCaseResponse"];
export type EvalRunSummary = AdminSchemas["EvalRunSummary"];
export type EvalRunResponse = AdminSchemas["EvalRunResponse"];
export type AdminConversationQuery = NonNullable<
  operations["list_conversations_admin_conversations_get"]["parameters"]["query"]
>;

export function getAdminOverview(days: number): Promise<AdminOverview> {
  return unwrap(api.GET("/admin/overview", { params: { query: { days } } }));
}

export function listAdminConversations(query: AdminConversationQuery): Promise<AdminConversationListResponse> {
  return unwrap(api.GET("/admin/conversations", { params: { query } }));
}

export function getAdminConversation(conversationId: string): Promise<AdminConversationDetail> {
  return unwrap(api.GET("/admin/conversations/{conversation_id}", {
    params: { path: { conversation_id: conversationId } },
  }));
}

export async function rescoreConversation(conversationId: string): Promise<{ queued: boolean }> {
  const response = await unwrap(api.POST("/admin/conversations/{conversation_id}/rescore", {
    params: { path: { conversation_id: conversationId } },
  }));
  return { queued: response.queued };
}

export async function createEvalCase(conversationId: string): Promise<{ eval_case_id: string }> {
  const response = await unwrap(api.POST("/admin/conversations/{conversation_id}/eval-case", {
    params: { path: { conversation_id: conversationId } },
  }));
  return { eval_case_id: response.eval_case_id };
}

export function listEvalCases(): Promise<EvalCaseResponse[]> {
  return unwrap(api.GET("/admin/eval-cases"));
}

export function listEvalRuns(): Promise<EvalRunSummary[]> {
  return unwrap(api.GET("/admin/eval-runs"));
}

export function getEvalRun(evalRunId: string): Promise<EvalRunResponse> {
  return unwrap(api.GET("/admin/eval-runs/{eval_run_id}", {
    params: { path: { eval_run_id: evalRunId } },
  }));
}
