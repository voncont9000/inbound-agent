export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: unknown,
  ) {
    super(message);
  }
}

function tenantHeaders(): HeadersInit {
  const headers: Record<string, string> = {};
  try {
    const memberships = JSON.parse(localStorage.getItem("inbound.memberships") ?? "[]") as Array<{ id: string }>;
    const selected = localStorage.getItem("inbound.tenant");
    if (memberships.length > 1 && selected) headers["X-Tenant-Id"] = selected;
  } catch {
    return headers;
  }
  return headers;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(tenantHeaders());
  new Headers(init.headers).forEach((value, key) => headers.set(key, value));
  const response = await fetch(`${window.location.origin}/api${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  const body: unknown = response.status === 204 ? undefined : await response.json().catch(() => undefined);
  if (!response.ok) {
    let message = "Something went wrong. Please try again.";
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail?: unknown }).detail;
      if (typeof detail === "string") message = detail;
      else if (Array.isArray(detail) && typeof detail[0]?.msg === "string") message = detail[0].msg;
    }
    throw new ApiError(message, response.status, body);
  }
  return body as T;
}
