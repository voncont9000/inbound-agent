import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { formatDuration, useTestCall } from "../call/TestCallProvider";
import styles from "./TestCallBar.module.css";

function PhoneIcon() {
  return <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z" /></svg>;
}

function ChevronIcon({ up }: { up: boolean }) {
  return <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{up ? <path d="m18 15-6-6-6 6" /> : <path d="m6 9 6 6 6-6" />}</svg>;
}

function HeadphonesIcon() {
  return <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M3 14v-3a9 9 0 0 1 18 0v3M5 14h2a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2Zm14 0h-2a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2Z" /></svg>;
}

function useTicker(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

export function TestCallBar() {
  const call = useTestCall();
  const { state } = call;
  const selectId = useId();
  const now = useTicker(state.phase === "in_call");
  if (!call.canTestCall) return null;
  const assistantName = call.assistants.find(({ id }) => id === (state.assistantId || call.selectedAssistantId))?.name ?? "your assistant";
  const elapsed = state.phase === "in_call" && state.connectedAt ? formatDuration(now - state.connectedAt) : null;
  const duration = state.connectedAt && state.endedAt ? formatDuration(state.endedAt - state.connectedAt) : null;
  const live = state.phase === "connecting" || state.phase === "in_call";
  const toggle = <button type="button" className={styles.toggle} aria-expanded={!call.collapsed} aria-controls="test-call-bar-content" onClick={() => call.setCollapsed(!call.collapsed)}>
    <ChevronIcon up={call.collapsed} /><span className="sr-only">{call.collapsed ? "Expand test call bar" : "Collapse test call bar"}</span>
  </button>;

  return <section className={`${styles.bar} ${call.collapsed ? styles.collapsed : ""}`} data-phase={state.phase} aria-label="Test call">
    <div className={styles.inner}>
      <div className={styles.status}>
        <span className={`${styles.dot} ${live ? styles.dotLive : ""} ${state.phase === "in_call" ? styles.dotPulse : ""}`} aria-hidden="true"><PhoneIcon /></span>
        <div className={styles.text} role="status" aria-live="polite">
          {state.phase === "idle" && (call.collapsed
            ? "Test call"
            : call.assistants.length > 1
              ? <>
                <label className="sr-only" htmlFor={selectId}>Assistant to test</label>
                <select id={selectId} className={styles.select} value={call.selectedAssistantId} onChange={(event) => call.selectAssistant(event.target.value)}>
                  {call.assistants.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
                </select>
              </>
              : <strong className={styles.assistantName}>{assistantName}</strong>)}
          {state.phase === "connecting" && <>Connecting to <strong className={styles.assistantName}>{assistantName}</strong>…</>}
          {state.phase === "in_call" && <>
            <span className={styles.inCallPrefix}>In call with</span>
            <strong className={styles.assistantName}>{assistantName}</strong>
            <time className={styles.timer} aria-label={`Call time ${elapsed}`}>{elapsed}</time>
            <span className={styles.headphones} title="Use headphones" aria-label="Use headphones"><HeadphonesIcon /><span>Use headphones</span></span>
            {state.muted && <span className={styles.mutedTag}>Muted</span>}
          </>}
          {state.phase === "ended" && <>Call ended{duration ? ` · ${duration}` : ""}</>}
          {state.phase === "error" && <span className={styles.error} role="alert">{state.error}</span>}
        </div>
      </div>
      <div id="test-call-bar-content" className={styles.controls} hidden={call.collapsed && state.phase === "idle"}>
        {state.phase === "idle" && <>
          <button type="button" className="button-accent button-sm" aria-label="Start test call" title="Use headphones" onClick={() => call.startCall()} disabled={!call.selectedAssistantId}>
            <PhoneIcon /> Test call
          </button>
        </>}
        {state.phase === "connecting" && <button type="button" className="button-sm" onClick={call.hangUp}>Cancel</button>}
        {state.phase === "in_call" && <>
          <button type="button" className="button-sm" aria-pressed={state.muted} onClick={call.toggleMute}>{state.muted ? "Unmute" : "Mute"}</button>
          <button type="button" className="button-danger button-sm" onClick={call.hangUp}>Hang up</button>
        </>}
        {(state.phase === "ended" || state.phase === "error") && <>
          {state.conversationId
            ? <Link className={styles.link} to={`/conversations/${state.conversationId}`}>View conversation</Link>
            : state.phase === "ended" && <Link className={styles.link} to="/inbox?channel=web">{state.conversationId === undefined ? "Finding conversation…" : "Open inbox"}</Link>}
          {state.phase === "error" && state.pageUrl && <a className={styles.link} href={state.pageUrl} target="_blank" rel="noopener">Open in new tab</a>}
          <button type="button" className="button-primary button-sm" onClick={() => call.startCall(state.assistantId)}>{state.phase === "error" ? "Try again" : "Call again"}</button>
          <button type="button" className="button-ghost button-sm" onClick={call.dismiss}>Dismiss</button>
        </>}
      </div>
      {toggle}
    </div>
  </section>;
}
