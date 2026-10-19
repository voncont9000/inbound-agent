export function normalizeWebsiteUrl(value: string): string {
  const normalized = value.trim();
  if (!normalized) return normalized;
  if (normalized.startsWith("//")) return `https:${normalized}`;
  if (!normalized.includes("://")) return `https://${normalized}`;
  return normalized;
}
