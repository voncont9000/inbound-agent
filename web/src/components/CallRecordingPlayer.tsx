import type { Schemas } from "../api/client";
import { Badge } from "./States";

export function CallRecordingPlayer({
  conversationId,
  recording,
}: {
  conversationId: string;
  recording: Schemas["CallRecordingSummary"] | null | undefined;
}) {
  if (!recording) return null;
  const duration = recording.duration_seconds == null
    ? "Duration unavailable"
    : `${Math.floor(recording.duration_seconds / 60)}:${String(Math.floor(recording.duration_seconds % 60)).padStart(2, "0")}`;
  return <section className="panel recording-panel" aria-label="Call recording">
    <div className="button-row">
      <h2>Call recording</h2>
      {recording.is_test && <Badge tone="neutral">Test</Badge>}
    </div>
    <p>{duration} · Deleted automatically on {new Date(recording.expires_at).toLocaleDateString()}</p>
    <audio
      controls
      preload="none"
      src={`${window.location.origin}/api/conversations/${conversationId}/recording`}
    />
  </section>;
}
