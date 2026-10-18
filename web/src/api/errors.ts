export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly body?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

export function fieldErrorsFrom422(body: unknown): Record<string, string> {
  if (!body || typeof body !== "object" || !("detail" in body)) return {};
  const detail = (body as { detail?: unknown }).detail;
  if (!Array.isArray(detail)) return {};
  const errors: Record<string, string> = {};
  for (const issue of detail) {
    if (!issue || typeof issue !== "object") continue;
    const entry = issue as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(entry.loc)) continue;
    const location = [...entry.loc];
    if (location[0] === "body") location.shift();
    if (location[0] === "config") location.shift();
    const path = location.map(String).join(".");
    if (path) errors[path] = typeof entry.msg === "string" ? entry.msg : "Invalid value";
  }
  return errors;
}

export function errorMessage(body: unknown, fallback = "Something went wrong. Please try again."): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && typeof detail[0]?.msg === "string") {
      return detail[0].msg;
    }
  }
  return fallback;
}
