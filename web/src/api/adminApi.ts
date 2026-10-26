import {
  type AdminConversationDetail,
  type AdminConversationList,
  type AdminConversationQuery,
  type AdminEvalCaseList,
  type AdminEvalRun,
  type AdminEvalRunList,
  type AdminOverview,
} from "./admin";
import { ApiError, errorMessage } from "./errors";

// Replace with `api.GET(...)` + unwrap once schema.d.ts is regenerated.
async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${window.location.origin}/api${path}`, {
    ...init,
    credentials: "include",
  });
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  if (!response.ok) {
    throw new ApiError(errorMessage(body), response.status, body);
  }
  return body as T;
}

export function getAdminOverview(days: number): Promise<AdminOverview> {
  return adminFetch(`/admin/overview?days=${days}`);
}

export function listAdminConversations(query: AdminConversationQuery): Promise<AdminConversationList> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  return adminFetch(`/admin/conversations?${params.toString()}`);
}

export function getAdminConversation(id: string): Promise<AdminConversationDetail> {
  return adminFetch(`/admin/conversations/${encodeURIComponent(id)}`);
}

export function rescoreConversation(id: string): Promise<void> {
  return adminFetch(`/admin/conversations/${encodeURIComponent(id)}/rescore`, { method: "POST" });
}

export function createEvalCase(id: string): Promise<{ eval_case_id: string }> {
  return adminFetch(`/admin/conversations/${encodeURIComponent(id)}/eval-case`, { method: "POST" });
}

export function listEvalCases(): Promise<AdminEvalCaseList> {
  return adminFetch("/admin/eval-cases");
}

export function listEvalRuns(): Promise<AdminEvalRunList> {
  return adminFetch("/admin/eval-runs");
}

export function getEvalRun(id: string): Promise<AdminEvalRun> {
  return adminFetch(`/admin/eval-runs/${encodeURIComponent(id)}`);
}
