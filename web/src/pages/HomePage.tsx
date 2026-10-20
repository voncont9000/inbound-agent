import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams, Link } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { ConversationsChart } from "../components/ConversationsChart";
import { channelLabels } from "../lib/dashboard";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "../components/States";
import styles from "../components/Dashboard.module.css";

type RangePreset = "today" | "7d" | "30d" | "90d" | "custom";
const rangeOptions: { value: RangePreset; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
  { value: "custom", label: "Custom" },
];

const localDate = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const daysAgo = (days: number) => localDate(new Date(Date.now() - days * 86_400_000));
const browserTimezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const percent = (part: number, whole: number) => whole ? `${Math.round((part / whole) * 100)}%` : "—";

function formatMinutes(minutes: number) {
  const rounded = Math.round(minutes);
  if (rounded < 60) return `${rounded} min`;
  return `${Math.floor(rounded / 60)} h ${rounded % 60} min`;
}

function formatMoney(value: number, currency: string) {
  if (value > 0 && value < 0.01) return `< ${new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(0.01)}`;
  return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(value);
}

function StatCard({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return <section className={styles.card} aria-label={label}>
    <h2 className={styles.label}>{label}</h2>
    <p className={styles.value}>{value}</p>
    {detail && <p className={styles.detail}>{detail}</p>}
  </section>;
}

function StatCards({ summary }: { summary: Schemas["DashboardSummary"] }) {
  const channels = (Object.keys(channelLabels) as (keyof typeof channelLabels)[])
    .filter((channel) => channel !== "web" || summary.by_channel.web > 0)
    .map((channel) => `${channelLabels[channel]} ${summary.by_channel[channel]}`)
    .join(" · ");
  const latency = summary.response_latency;
  const cost = summary.cost;
  return <div className={styles.stats}>
    <StatCard label="Conversations" value={String(summary.conversations)} detail={channels} />
    <StatCard label="Handled by assistant" value={String(summary.handled_by_assistant)} detail={`${percent(summary.handled_by_assistant, summary.conversations)} · ${summary.passed_to_staff} passed to staff`} />
    <StatCard label="Bookings made" value={String(summary.bookings)} />
    <StatCard label="Callbacks & tasks" value={String(summary.tasks)} detail={`${summary.callbacks} callback${summary.callbacks === 1 ? "" : "s"} requested`} />
    <StatCard label="Estimated time saved" value={formatMinutes(summary.time_saved_minutes)} detail="Estimated staff minutes per conversation, by channel" />
    <StatCard label="Median response time" value={latency.median_seconds == null ? "—" : `${latency.median_seconds.toFixed(1)} s`} detail={latency.calls_measured ? `Across ${latency.calls_measured} measured call${latency.calls_measured === 1 ? "" : "s"}` : "No measured calls"} />
    <StatCard label="AI & telephony cost" value={formatMoney(cost.total, cost.currency)} detail={`AI ${formatMoney(cost.ai, cost.currency)} · Telephony ${formatMoney(cost.telephony, cost.currency)}`} />
  </div>;
}

export function HomePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const range = (rangeOptions.find(({ value }) => value === searchParams.get("range"))?.value ?? "7d") as RangePreset;
  const assistantId = searchParams.get("assistant") ?? "";
  const start = searchParams.get("start") ?? daysAgo(6);
  const end = searchParams.get("end") ?? daysAgo(0);
  const customInvalid = range === "custom" && (!start || !end || start > end);
  const timezone = browserTimezone();
  const assistants = useQuery({ queryKey: ["assistants"], queryFn: () => unwrap(api.GET("/assistants")) });
  const query = {
    range: range === "90d" ? "custom" : range,
    timezone,
    assistant_id: assistantId || undefined,
    ...(range === "90d" ? { start: daysAgo(89), end: daysAgo(0) } : {}),
    ...(range === "custom" ? { start, end } : {}),
  };
  const summary = useQuery({
    queryKey: ["dashboard-summary", query],
    queryFn: () => unwrap(api.GET("/dashboard/summary", { params: { query } })),
    enabled: !customInvalid,
    placeholderData: keepPreviousData,
  });

  function update(values: Record<string, string | null>) {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setSearchParams(next, { replace: true });
  }

  const data = summary.data;
  return <main className="page" id="main-content">
    <PageHeader title="Home" lede="How your assistants are handling inbound conversations." />
    {assistants.data && assistants.data.items.length === 0 && <section className="panel first-run" aria-labelledby="first-run-heading">
      <h2 id="first-run-heading">Create your first assistant</h2>
      <p className="muted">Nothing is answering yet. Set up an assistant from your website or a template; it takes a couple of minutes.</p>
      <div className="button-row"><Link className="button-primary" to="/assistants/new">Set up from website</Link><Link className="button-secondary" to="/assistants">Create from template</Link></div>
    </section>}
    <div className="toolbar">
      <div className="range-control">
        <span className="range-control-label">Time range</span>
        <div className="range-segments" role="group" aria-label="Time range">
          {rangeOptions.map(({ value, label }) => <button
            type="button"
            key={value}
            aria-pressed={range === value}
            className={range === value ? "range-segment range-segment-active" : "range-segment"}
            onClick={() => update(value === "custom"
              ? { range: "custom", start, end }
              : { range: value === "7d" ? null : value, start: null, end: null })}
          >{label}</button>)}
        </div>
      </div>
      {range === "custom" && <>
        <div className="field"><label htmlFor="dashboard-start">From</label><input id="dashboard-start" type="date" value={start} max={end} onChange={(event) => update({ start: event.target.value })} /></div>
        <div className="field"><label htmlFor="dashboard-end">To</label><input id="dashboard-end" type="date" value={end} min={start} onChange={(event) => update({ end: event.target.value })} /></div>
      </>}
      <div className="field"><label htmlFor="dashboard-assistant">Assistant</label>
        <select id="dashboard-assistant" value={assistantId} onChange={(event) => update({ assistant: event.target.value || null })}>
          <option value="">All assistants</option>
          {assistants.data?.items.map((assistant) => <option key={assistant.id} value={assistant.id}>{assistant.name}</option>)}
        </select>
      </div>
    </div>
    {customInvalid && <ErrorState message="Choose a start date on or before the end date." />}
    {!customInvalid && summary.isLoading && <LoadingState label="Loading overview…" />}
    {!customInvalid && summary.error && <ErrorState message="The overview could not be loaded." />}
    {!customInvalid && data && <>
      <StatCards summary={data} />
      <section className="panel" aria-labelledby="dashboard-chart-title">
        <h2 id="dashboard-chart-title">Conversations over time</h2>
        {data.conversations
          ? <ConversationsChart series={data.series} bucket={data.range.bucket} timezone={data.range.timezone} />
          : <EmptyState>No conversations in this period yet. Practice calls are not counted.</EmptyState>}
      </section>
      <p className="help">Practice calls are excluded. Time saved assumes {Object.entries(data.minutes_saved_per_conversation).map(([channel, minutes]) => `${minutes} min per ${channelLabels[channel as keyof typeof channelLabels] ?? channel} conversation`).join(", ")}, with {Math.round(data.passed_to_staff_credit * 100)}% credit when a conversation is passed to staff.</p>
    </>}
  </main>;
}
