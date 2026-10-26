import type { AdminOverview } from "../api/adminApi";
import { niceMax } from "../lib/dashboard";
import styles from "./Dashboard.module.css";
import moduleStyles from "./Admin.module.css";

const width = 720;
const height = 240;
const pad = { top: 16, right: 36, bottom: 34, left: 38 };

export function AdminQualityChart({ series }: { series: AdminOverview["by_day"] }) {
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const slot = plotWidth / Math.max(series.length, 1);
  const barWidth = Math.max(2, Math.min(24, slot * 0.66));
  const maxCount = niceMax(Math.max(0, ...series.map(({ conversations }) => conversations)));
  const yCount = (value: number) => pad.top + plotHeight - (value / maxCount) * plotHeight;
  const yScore = (value: number) => pad.top + plotHeight - ((value - 1) / 4) * plotHeight;
  const labelEvery = Math.max(1, Math.ceil(series.length / 8));
  const total = series.reduce((sum, point) => sum + point.conversations, 0);
  const reviewTotal = series.reduce((sum, point) => sum + point.needs_review, 0);
  const avgPoints = series.flatMap((point, index) => point.avg_overall == null
    ? []
    : [{ x: pad.left + index * slot + slot / 2, y: yScore(point.avg_overall) }]);
  const line = avgPoints.map(({ x, y }, index) => `${index ? "L" : "M"}${x},${y}`).join(" ");
  const ticks = [0, maxCount];
  const dateFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

  return <figure style={{ margin: 0 }}>
    <ul className={styles.legend}>
      <li><span className={styles.swatch} style={{ background: "var(--border-control)" }} />Conversations</li>
      <li><span className={styles.swatch} style={{ background: "var(--accent)" }} />Needs review</li>
      <li><span className={moduleStyles.lineSwatch} />Average score</li>
    </ul>
    <svg className={styles.chart} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Quality over time: ${total} conversations, ${reviewTotal} need review, across ${series.length} days`}>
      {ticks.map((tick) => <g key={tick}>
        <line className={styles.grid} x1={pad.left} x2={width - pad.right} y1={yCount(tick)} y2={yCount(tick)} />
        <text className={styles.axis} x={pad.left - 7} y={yCount(tick) + 4} textAnchor="end">{tick}</text>
      </g>)}
      {[1, 3, 5].map((score) => <text key={score} className={styles.axis} x={width - pad.right + 7} y={yScore(score) + 4}>{score}</text>)}
      {series.map((point, index) => {
        const x = pad.left + index * slot + (slot - barWidth) / 2;
        const nonReview = point.conversations - point.needs_review;
        const label = dateFormat.format(new Date(`${point.date}T00:00:00Z`));
        return <g key={point.date} data-testid="chart-bar">
          <title>{`${label}: ${point.conversations} conversations, ${point.needs_review} need review${point.avg_overall == null ? "" : `, average score ${point.avg_overall.toFixed(1)}`}`}</title>
          {nonReview > 0 && <rect x={x} y={yCount(nonReview)} width={barWidth} height={yCount(0) - yCount(nonReview)} fill="var(--border-control)" rx={1.5} />}
          {point.needs_review > 0 && <rect x={x} y={yCount(point.conversations)} width={barWidth} height={yCount(nonReview) - yCount(point.conversations)} fill="var(--accent)" rx={1.5} />}
          {index % labelEvery === 0 && <text className={styles.axis} x={x + barWidth / 2} y={height - 8} textAnchor="middle">{label}</text>}
        </g>;
      })}
      {line && <path d={line} fill="none" stroke="var(--ink)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />}
      {avgPoints.map(({ x, y }, index) => <circle key={`${x}-${index}`} cx={x} cy={y} r="2.5" fill="var(--ink)" />)}
    </svg>
  </figure>;
}
