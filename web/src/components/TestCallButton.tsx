import { useMutation } from "@tanstack/react-query";
import { api, unwrap } from "../api/client";
import { ApiError } from "../api/errors";

function errorDetail(error: unknown): string {
  if (error instanceof ApiError && error.body && typeof error.body === "object" && "detail" in error.body) {
    const detail = (error.body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return error instanceof Error ? error.message : "Unable to create a test call.";
}

export function TestCallButton({ assistantId }: { assistantId: string }) {
  const testCall = useMutation({
    mutationFn: () => unwrap(api.POST("/assistants/{assistant_id}/test-calls", {
      params: { path: { assistant_id: assistantId } },
    })),
  });

  return <div className="stack">
    <button type="button" onClick={() => testCall.mutate()} disabled={testCall.isPending}>
      {testCall.isPending ? "Creating test call…" : "Test call"}
    </button>
    {testCall.data && <>
      <a href={testCall.data.url} target="_blank" rel="noopener">Open test call</a>
      <span className="muted">Link valid for 10 minutes</span>
    </>}
    {testCall.error && <p role="alert">{errorDetail(testCall.error)}</p>}
  </div>;
}
