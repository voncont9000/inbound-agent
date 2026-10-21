import type { Schemas } from "../api/client";

export type IncludeRule = { field: "from" | "to" | "subject"; contains: string };

export function channelLabel(kind: Schemas["ChannelConnectionResponse"]["kind"]) {
  if (kind === "gmail") return "Email (Gmail)";
  if (kind === "whatsapp") return "WhatsApp";
  if (kind === "web") return "Browser";
  return "Phone";
}

export function channelStatusTone(status: string): "neutral" | "success" | "warning" | "danger" | "info" {
  if (status.toLowerCase() === "active") return "success";
  if (status.toLowerCase() === "failed") return "danger";
  return "neutral";
}

export function readIncludeRules(settings: Record<string, unknown>): IncludeRule[] {
  const value = settings.include_rules;
  if (!Array.isArray(value)) return [];
  return value.flatMap((item): IncludeRule[] => {
    if (!item || typeof item !== "object") return [];
    const { field, contains } = item as Record<string, unknown>;
    if ((field !== "from" && field !== "to" && field !== "subject") || typeof contains !== "string") {
      return [];
    }
    return [{ field, contains }];
  });
}

export function describeRules(rules: IncludeRule[]) {
  if (!rules.length) return "All emails";
  const summary = rules.map(({ field, contains }) => {
    const label = field === "to" ? "To" : field === "from" ? "From" : "Subject";
    return `${label} contains ${contains}`;
  }).join("; ");
  return `Only emails where: ${summary}`;
}
