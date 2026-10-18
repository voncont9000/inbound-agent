import createClient from "openapi-fetch";
import type { paths } from "./schema";
import { errorMessage } from "./errors";
import { ApiError } from "./request";

export type Schemas = import("./schema").components["schemas"];

export const api = createClient<paths>({
  baseUrl: `${window.location.origin}/api`,
  credentials: "include",
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

export async function uploadKnowledgeFile(assistantId: string, file: File) {
  const result = await api.POST("/assistants/{assistant_id}/knowledge/sources", {
    params: { path: { assistant_id: assistantId } },
    body: { file: file as unknown as string },
    bodySerializer: () => {
      const form = new FormData();
      form.set("file", file);
      return form;
    },
  });
  if (result.error) {
    throw new ApiError(errorMessage(result.error), result.response.status, result.error);
  }
  return result.data;
}
