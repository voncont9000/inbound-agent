import { useEffect, useState } from "react";
import { useReducedMotion } from "../hooks/motion";
import { Icon } from "../components/Icon";
import { SoundBars } from "../components/ui";

const LINES = [
  "Thanks for calling. How can I help you today?",
  "I can take your details and have the team call you back.",
  "You're booked in. You'll get a confirmation by email.",
];

function formatTime(seconds: number) {
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

export function LiveCallCard() {
  const reduced = useReducedMotion();
  const [seconds, setSeconds] = useState(42);
  const [line, setLine] = useState(0);

  useEffect(() => {
    if (reduced) return;
    const tick = window.setInterval(() => setSeconds((value) => (value >= 599 ? 0 : value + 1)), 1000);
    const rotate = window.setInterval(() => setLine((value) => (value + 1) % LINES.length), 4200);
    return () => {
      window.clearInterval(tick);
      window.clearInterval(rotate);
    };
  }, [reduced]);

  return (
    <div className="live-card" aria-hidden="true">
      <div className="live-card-head">
        <span className="live-badge">
          <span className="live-dot" />
          Live
        </span>
        <span className="live-card-meta">
          <Icon name="phone" width={14} height={14} />
          Incoming call
        </span>
        <span className="live-card-time">{formatTime(seconds)}</span>
      </div>
      <div className="live-card-body">
        <span className="live-avatar">
          <SoundBars className="sound-bars--live" />
        </span>
        <div className="live-card-speech">
          <span className="live-card-who">Assistant is speaking</span>
          <p key={line} className="live-card-line">
            {LINES[line]}
          </p>
        </div>
      </div>
      <div className="live-wave">
        {Array.from({ length: 28 }, (_, index) => (
          <span key={index} style={{ animationDelay: `${(index * 97) % 900}ms` }} />
        ))}
      </div>
    </div>
  );
}
