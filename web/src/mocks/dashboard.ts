import type { Schemas } from "../api/client";

export type DashboardMockQuery = {
  range: "today" | "7d" | "30d" | "custom";
  start?: string | null;
  end?: string | null;
  timezone?: string;
  assistant_id?: string | null;
  include_test?: boolean;
};

const hourMs = 3_600_000;
const dayMs = 24 * hourMs;
export const minutesSavedPerConversation = { phone: 5, web: 5, whatsapp: 4, gmail: 6 };
const passedToStaffCredit = 0.5;

const noise = (key: number) => {
  const value = Math.sin(key * 12.9898) * 43758.5453;
  return value - Math.floor(value);
};
const round = (value: number, digits = 4) => Number(value.toFixed(digits));

function window(query: DashboardMockQuery, now: Date) {
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  if (query.range === "custom" && query.start && query.end) {
    const start = Date.parse(`${query.start}T00:00:00Z`);
    return { start, days: Math.round((Date.parse(`${query.end}T00:00:00Z`) - start) / dayMs) + 1 };
  }
  const days = query.range === "today" ? 1 : query.range === "30d" ? 30 : 7;
  return { start: today - (days - 1) * dayMs, days };
}

/** Deterministic demo analytics for the mock API; `seed` varies the numbers per tenant, `scale` 0 yields the empty state. */
export function mockDashboardSummary(query: DashboardMockQuery, seed = 1, scale = 1, now = new Date()): Schemas["DashboardSummary"] {
  const { start, days } = window(query, now);
  const hourly = days === 1;
  const stepMs = hourly ? hourMs : dayMs;
  const series = Array.from({ length: hourly ? 24 : days }, (_, index) => {
    const pointStart = start + index * stepMs;
    const weekday = new Date(pointStart).getUTCDay();
    const busy = hourly ? (index >= 8 && index <= 18 ? 1 : 0.1) : weekday === 0 || weekday === 6 ? 0.35 : 1;
    const key = index + seed * 31;
    const phone = Math.round(scale * busy * (hourly ? 2 : 9) * (0.6 + noise(key)));
    const whatsapp = Math.round(scale * busy * (hourly ? 1 : 4) * (0.5 + noise(key + 7)));
    const gmail = Math.round(scale * busy * (hourly ? 1 : 3) * (0.4 + noise(key + 13)));
    return { start: new Date(pointStart).toISOString(), phone, web: 0, whatsapp, gmail, total: phone + whatsapp + gmail };
  });
  const byChannel = { phone: 0, web: 0, whatsapp: 0, gmail: 0 };
  for (const point of series) {
    byChannel.phone += point.phone;
    byChannel.whatsapp += point.whatsapp;
    byChannel.gmail += point.gmail;
  }
  const conversations = byChannel.phone + byChannel.whatsapp + byChannel.gmail;
  const passed = Math.round(conversations * 0.22);
  const callbacks = Math.round(passed * 0.6);
  const fullCredit = (Object.keys(byChannel) as (keyof typeof byChannel)[])
    .reduce((sum, channel) => sum + minutesSavedPerConversation[channel] * byChannel[channel], 0);
  const passedShare = conversations ? passed / conversations : 0;
  const telephony = byChannel.phone * 0.0142;
  const ai = byChannel.phone * 0.0281 + byChannel.whatsapp * 0.0012 + byChannel.gmail * 0.0016;
  return {
    range: {
      preset: query.range,
      start: new Date(start).toISOString(),
      end: new Date(start + days * dayMs).toISOString(),
      timezone: query.timezone ?? "UTC",
      bucket: hourly ? "hour" : "day",
    },
    assistant_id: query.assistant_id ?? null,
    include_test: query.include_test ?? false,
    conversations,
    by_channel: byChannel,
    handled_by_assistant: conversations - passed,
    passed_to_staff: passed,
    bookings: Math.round(byChannel.phone * 0.18),
    callbacks,
    tasks: callbacks + Math.round(passed * 0.5),
    time_saved_minutes: round(fullCredit * (1 - passedShare * (1 - passedToStaffCredit)), 1),
    minutes_saved_per_conversation: minutesSavedPerConversation,
    passed_to_staff_credit: passedToStaffCredit,
    response_latency: byChannel.phone
      ? { median_seconds: round(1.38 + seed * 0.07, 3), calls_measured: byChannel.phone }
      : { median_seconds: null, calls_measured: 0 },
    cost: {
      currency: "USD",
      total: round(ai + telephony),
      ai: round(ai),
      telephony: round(telephony),
      conversations_costed: conversations,
    },
    series,
  };
}
