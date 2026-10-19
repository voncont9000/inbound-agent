import type { Schemas } from "../api/client";
import { formatDate } from "./date";

export type TranscriptRow = {
  id: string;
  role: "caller" | "assistant";
  speaker: string;
  text: string;
  ts: string;
  time: string;
  draft: boolean;
};

function voiceOffset(ts: string, startedAt: string): string {
  const timestamp = Date.parse(ts);
  const start = Date.parse(startedAt);
  const elapsed = Number.isFinite(timestamp) && Number.isFinite(start)
    ? Math.max(0, Math.floor((timestamp - start) / 1000))
    : 0;
  const hours = Math.floor(elapsed / 3600);
  const minutes = Math.floor((elapsed % 3600) / 60);
  const seconds = elapsed % 60;
  const paddedSeconds = String(seconds).padStart(2, "0");

  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${paddedSeconds}`
    : `${Math.floor(elapsed / 60)}:${paddedSeconds}`;
}

export function transcriptRows(
  messages: Schemas["MessageResponse"][],
  channel: string,
  startedAt: string,
): TranscriptRow[] {
  const isVoice = channel === "phone" || channel === "web";
  const rows: TranscriptRow[] = [];
  let previousFragmentTs: number | undefined;

  for (const message of messages) {
    const role = message.role === "assistant" ? "assistant" : "caller";
    const timestamp = Date.parse(message.ts);
    const previous = rows.at(-1);
    const gap = timestamp - (previousFragmentTs ?? Number.NaN);

    if (
      isVoice
      && previous
      && previous.role === role
      && Number.isFinite(timestamp)
      && previousFragmentTs !== undefined
      && Number.isFinite(previousFragmentTs)
      && gap >= 0
      && gap <= 2000
    ) {
      previous.text = `${previous.text.trimEnd()} ${message.text.trimStart()}`;
      previousFragmentTs = timestamp;
      continue;
    }

    rows.push({
      id: message.id,
      role,
      speaker: role === "assistant" ? "Assistant" : isVoice ? "Caller" : "Customer",
      text: message.text,
      ts: message.ts,
      time: isVoice ? voiceOffset(message.ts, startedAt) : formatDate(message.ts),
      draft: message.meta.draft === true,
    });
    previousFragmentTs = timestamp;
  }

  return rows;
}
