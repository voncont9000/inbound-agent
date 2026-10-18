import type { Schemas } from "../api/client";
import { channelLabels, type DashboardChannel as Channel } from "../lib/dashboard";
import styles from "./Dashboard.module.css";

type Point = Schemas["DashboardSeriesPoint"];
const colors: Record<Channel, string> = { phone: "#1d6d5b", whatsapp: "#55b38a", gmail: "#c98a2b", web: "#7a8fa6" };
const width = 720;
const height = 240;
const pad = { top: 12, right: 8, bottom: 28, left: 36 };

function niceMax(value: number) {
  if (value <= 4) return 4;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].map((factor) => factor * magnitude).find((candidate) => value / candidate <= 4) ?? 10 * magnitude;
  return Math.ceil(value / step) * step;
}

export function ConversationsChart({ series, bucket, timezone }: { series: Point[]; bucket: "hour" | "day"; timezone: string }) {
  const channels = (Object.keys(channelLabels) as Channel[]).filter((channel) => channel !== "web" || series.some((point) => point.web > 0));
  const max = niceMax(Math.max(0, ...series.map((point) => point.total)));
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const slot = plotWidth / Math.max(series.length, 1);
  const barWidth = Math.max(2, Math.min(28, slot * 0.7));
  const y = (value: number) => pad.top + plotHeight - (value / max) * plotHeight;
  const format = new Intl.DateTimeFormat("en-GB", bucket === "hour"
    ? { hour: "2-digit", minute: "2-digit", timeZone: timezone }
    : { day: "numeric", month: "short", timeZone: timezone });
  const labelEvery = Math.max(1, Math.ceil(series.length / 8));
  const total = series.reduce((sum, point) => sum + point.total, 0);
  const ticks = [0, max / 2, max];
  return <figure style={{ margin: 0 }}>
    <ul className={styles.legend}>
      {channels.map((channel) => <li key={channel}><span className={styles.swatch} style={{ background: colors[channel] }} />{channelLabels[channel]}</li>)}
    </ul>
    <svg className={styles.chart} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Conversations over time: ${total} in ${series.length} ${bucket === "hour" ? "hours" : "days"}`}>
      {ticks.map((tick) => <g key={tick}>
        <line className={styles.grid} x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} />
        <text className={styles.axis} x={pad.left - 6} y={y(tick) + 4} textAnchor="end">{tick}</text>
      </g>)}
      {series.map((point, index) => {
        const x = pad.left + index * slot + (slot - barWidth) / 2;
        let offset = 0;
        const label = format.format(new Date(point.start));
        return <g key={point.start} data-testid="chart-bar">
          <title>{`${label}: ${point.total} (${channels.map((channel) => `${channelLabels[channel]} ${point[channel]}`).join(", ")})`}</title>
          {channels.map((channel) => {
            const value = point[channel];
            if (!value) return null;
            const top = y(offset + value);
            const barHeight = y(offset) - top;
            offset += value;
            return <rect key={channel} x={x} y={top} width={barWidth} height={barHeight} fill={colors[channel]} rx={1.5} />;
          })}
          {index % labelEvery === 0 && <text className={styles.axis} x={x + barWidth / 2} y={height - 8} textAnchor="middle">{label}</text>}
        </g>;
      })}
    </svg>
  </figure>;
}
