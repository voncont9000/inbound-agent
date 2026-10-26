export function normalizeWebsiteUrl(value: string): string {
  const normalized = value.trim();
  if (!normalized) return normalized;
  if (normalized.startsWith("//")) return `https:${normalized}`;
  if (!normalized.includes("://")) return `https://${normalized}`;
  return normalized;
}

export function businessNameFromWebsite(value: string): string {
  const input = value.trim();
  if (!input) return "";
  const hasScheme = /^[a-z][a-z\d+.-]*:/i.test(input);
  const isDomainWithPort = /^[^/]+:\d+(?:\/|$)/.test(input);
  if (hasScheme && !isDomainWithPort && !/^https?:\/\//i.test(input) && !input.startsWith("//")) return "";

  const normalized = normalizeWebsiteUrl(input);

  try {
    const website = new URL(normalized);
    if (website.protocol !== "http:" && website.protocol !== "https:") return "";
    const hostname = website.hostname.replace(/\.$/, "").replace(/^www\./i, "");
    const labels = hostname.split(".");
    if (!hostname || labels.some((label) => !/^[a-z0-9_-]+$/i.test(label))) return "";

    const firstLabel = labels[0].replace(/[-_]+/g, " ").trim();
    if (!firstLabel) return "";
    return firstLabel.split(/\s+/).map((word) => word[0].toUpperCase() + word.slice(1).toLowerCase()).join(" ");
  } catch {
    return "";
  }
}
