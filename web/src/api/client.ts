import createClient from "openapi-fetch";
import type { paths } from "./schema";
import { ApiError, errorMessage } from "./errors";

export type Schemas = import("./schema").components["schemas"];

type ApiResult<T> = {
  data?: T;
  error?: unknown;
  response: Response;
};

export const api = createClient<paths>({
  baseUrl: `${window.location.origin}/api`,
  credentials: "include",
  fetch: (...args) => globalThis.fetch(...args),
});

api.use({
  onRequest({ request }) {
    if (typeof localStorage === "undefined") return request;
    try {
      const memberships = JSON.parse(
        localStorage.getItem("inbound.memberships") ?? "[]",
      ) as Array<{ id: string }>;
      const selected = localStorage.getItem("inbound.tenant");
      if (memberships.length > 1 && selected) {
        request.headers.set("X-Tenant-Id", selected);
      }
    } catch {
      return request;
    }
    return request;
  },
});

export async function unwrap<T>(promise: Promise<ApiResult<T>>): Promise<T> {
  const result = await promise;
  if (result.error !== undefined || !result.response.ok) {
    throw new ApiError(errorMessage(result.error), result.response.status, result.error);
  }
  if (result.data === undefined) {
    throw new ApiError("The server returned an empty response.", result.response.status);
  }
  return result.data;
}

export async function unwrapNoContent(promise: Promise<ApiResult<unknown>>): Promise<void> {
  const result = await promise;
  if (result.error !== undefined || !result.response.ok) {
    throw new ApiError(errorMessage(result.error), result.response.status, result.error);
  }
}

export async function uploadKnowledgeFile(assistantId: string, file: File) {
  return unwrap(api.POST("/assistants/{assistant_id}/knowledge/sources", {
    params: { path: { assistant_id: assistantId } },
    body: { file: file.name },
    bodySerializer: () => {
      const form = new FormData();
      form.set("file", file);
      return form;
    },
  }));
}
