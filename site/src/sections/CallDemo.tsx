import { useEffect, useState } from "react";
import { useInView, useReducedMotion } from "../hooks/motion";
import { Icon } from "../components/Icon";
import { SoundBars } from "../components/ui";

type Turn = { who: "assistant" | "caller"; text: string; reveals?: "name" | "phone" | "reason" };

const TURNS: Turn[] = [
  { who: "assistant", text: "Good morning, thanks for calling. How can I help?" },
  { who: "caller", text: "Hi, I have an appointment on Thursday and need to move it." },
  { who: "assistant", text: "Of course. Can I take your name, please?" },
  { who: "caller", text: "It's Sam Taylor.", reveals: "name" },
  { who: "assistant", text: "Thanks, Sam. What's the best number to reach you on?" },
  { who: "caller", text: "07700 900123.", reveals: "phone" },
  { who: "assistant", text: "And when would suit you instead?" },
  { who: "caller", text: "Friday afternoon, if that works.", reveals: "reason" },
  { who: "assistant", text: "Noted. Someone from the team will call you back today to confirm Friday." },
];

const DETAILS = [
  { key: "name", label: "Name", value: "Sam Taylor" },
  { key: "phone", label: "Phone", value: "07700 900123" },
  { key: "reason", label: "Reason", value: "Move Thursday appointment to Friday afternoon" },
] as const;

const DONE = TURNS.length;
const CHAR_MS = 22;

type State = { turn: number; chars: number; extras: number };
const COMPLETE: State = { turn: DONE, chars: 0, extras: 2 };

export function CallDemo() {
  const [ref, inView] = useInView<HTMLDivElement>(0.3);
  const reduced = useReducedMotion();
  const [state, setState] = useState<State>(COMPLETE);
  const [started, setStarted] = useState(false);

  useEffect(() => {
    if (reduced) {
      setState(COMPLETE);
      return;
    }
    if (inView && !started) {
      setStarted(true);
      setState({ turn: 0, chars: 0, extras: 0 });
    }
  }, [inView, reduced, started]);

  useEffect(() => {
    if (reduced || !inView || !started) return;
    let delay: number;
    let next: State;
    if (state.turn < DONE) {
      const text = TURNS[state.turn].text;
      if (state.chars < text.length) {
        delay = state.chars === 0 ? (TURNS[state.turn].who === "caller" ? 700 : 500) : CHAR_MS;
        next = { ...state, chars: Math.min(text.length, state.chars + 2) };
      } else {
        delay = 650;
        next = { turn: state.turn + 1, chars: 0, extras: 0 };
      }
    } else if (state.extras < 2) {
      delay = 700;
      next = { ...state, extras: state.extras + 1 };
    } else {
      delay = 4200;
      next = { turn: 0, chars: 0, extras: 0 };
    }
    const timer = window.setTimeout(() => setState(next), delay);
    return () => window.clearTimeout(timer);
  }, [state, inView, reduced, started]);

  const shown = TURNS.slice(0, Math.min(state.turn + 1, DONE));
  const revealed = new Set(TURNS.slice(0, state.turn).map((turn) => turn.reveals).filter(Boolean));
  const typing = state.turn < DONE;
  const speaking = typing && TURNS[state.turn].who === "assistant" && state.chars > 0;

  return (
    <div className="demo" ref={ref}>
      <div className="demo-window" role="img" aria-label="Example call: a caller asks to move an appointment. The assistant collects the caller's name, phone number and reason, creates a call-back task and emails the team.">
        <div className="demo-bar" aria-hidden="true">
          <span className="demo-bar-title">
            <Icon name="phone" width={16} height={16} />
            Front desk line
          </span>
          <span className="live-badge">
            <span className="live-dot" />
            {typing ? "Live" : "Ended"}
          </span>
        </div>
        <div className="demo-grid" aria-hidden="true">
          <div className="demo-transcript">
            <div className="demo-lines">
              {shown.map((turn, index) => {
                const current = index === state.turn;
                const text = current ? turn.text.slice(0, state.chars) : turn.text;
                if (current && state.chars === 0) {
                  return (
                    <div key={index} className={`demo-line demo-line--${turn.who}`}>
                      <span className="demo-who">{turn.who === "assistant" ? "Assistant" : "Caller"}</span>
                      <span className="demo-bubble demo-bubble--typing">
                        <span />
                        <span />
                        <span />
                      </span>
                    </div>
                  );
                }
                return (
                  <div key={index} className={`demo-line demo-line--${turn.who}`}>
                    <span className="demo-who">{turn.who === "assistant" ? "Assistant" : "Caller"}</span>
                    <span className="demo-bubble">
                      {text}
                      {current && <span className="demo-caret" />}
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="demo-status">
              <span className="demo-avatar">
                <SoundBars className={speaking ? "sound-bars--live" : undefined} />
              </span>
              {typing ? (speaking ? "Assistant is speaking" : "Listening") : "Call ended · transcript saved"}
            </div>
          </div>
          <div className="demo-side">
            <div className="demo-panel">
              <p className="demo-panel-title">
                <Icon name="user" width={16} height={16} />
                Caller details
              </p>
              <dl className="demo-fields">
                {DETAILS.map((detail) => {
                  const filled = revealed.has(detail.key);
                  return (
                    <div key={detail.key} className="demo-field" data-filled={filled}>
                      <dt>{detail.label}</dt>
                      <dd>{filled ? detail.value : <span className="demo-placeholder" />}</dd>
                    </div>
                  );
                })}
              </dl>
            </div>
            <div className="demo-task" data-visible={state.turn >= DONE && state.extras >= 1}>
              <span className="demo-task-icon">
                <Icon name="task" width={18} height={18} />
              </span>
              <div>
                <p className="demo-task-kicker">Task created</p>
                <p className="demo-task-title">Call back Sam Taylor</p>
                <p className="demo-task-sub">Confirm Friday afternoon · Today</p>
              </div>
            </div>
            <div className="demo-chip" data-visible={state.turn >= DONE && state.extras >= 2}>
              <Icon name="check" width={14} height={14} />
              Email sent to the team
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
