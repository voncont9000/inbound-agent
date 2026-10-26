export type DashboardChannel = "phone" | "whatsapp" | "gmail" | "web";

export const channelLabels: Record<DashboardChannel, string> = { phone: "Phone", whatsapp: "WhatsApp", gmail: "Gmail", web: "Web" };

export function niceMax(value: number) {
  if (value <= 4) return 4;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].map((factor) => factor * magnitude).find((candidate) => value / candidate <= 4) ?? 10 * magnitude;
  return Math.ceil(value / step) * step;
}
