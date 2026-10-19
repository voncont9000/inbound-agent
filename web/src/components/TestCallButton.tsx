import { useTestCall } from "../call/TestCallProvider";

/** Starts a browser test call in the global test-call bar. Hidden for roles that cannot run test calls. */
export function TestCallButton({ assistantId }: { assistantId: string }) {
  const call = useTestCall();
  if (!call.canTestCall) return null;
  const busy = call.state.assistantId === assistantId && (call.state.phase === "connecting" || call.state.phase === "in_call");
  return <button type="button" className="button-secondary" onClick={() => call.startCall(assistantId)} disabled={busy}>
    {busy ? (call.state.phase === "in_call" ? "In test call" : "Connecting…") : "Test call"}
  </button>;
}
