import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Icon, type IconName } from "../components/Icon";
import { Reveal } from "../components/Reveal";
import { Button } from "../components/ui";
import { appLink } from "../config";

type UseCase = { id: string; label: string; icon: IconName; title: string; text: string; bullets: string[]; mock: ReactNode };

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

function MockRow({ icon, title, sub, tag }: { icon: IconName; title: string; sub: string; tag?: string }) {
  return (
    <div className="uc-row">
      <span className="uc-row-icon">
        <Icon name={icon} width={16} height={16} />
      </span>
      <span className="uc-row-text">
        <span className="uc-row-title">{title}</span>
        <span className="uc-row-sub">{sub}</span>
      </span>
      {tag && <span className="uc-tag">{tag}</span>}
    </div>
  );
}

const USE_CASES: UseCase[] = [
  {
    id: "front-desk",
    label: "Front desk",
    icon: "building",
    title: "A front desk that never misses a call",
    text: "Yelvo greets every caller, answers questions from your knowledge, and puts them through to the right person when they need one.",
    bullets: ["Answers calls when the line is busy", "Forwards to a colleague when asked", "Recognises returning callers"],
    mock: (
      <>
        <MockRow icon="phone" title="Incoming call" sub="Asked for the office manager" tag="Forwarding" />
        <MockRow icon="forward" title="Put through to Alex" sub="Summary shared before the handover" />
      </>
    ),
  },
  {
    id: "customer-service",
    label: "Customer service",
    icon: "headset",
    title: "Answers for customers, on every channel",
    text: "The same assistant replies on the phone, in Gmail and on WhatsApp, using the facts and FAQs you give it.",
    bullets: ["Replies to Gmail and WhatsApp", "Answers from your knowledge and FAQs", "Says so when it doesn't know, and creates a task"],
    mock: (
      <>
        <MockRow icon="chat" title="WhatsApp · Do you open on Saturdays?" sub="Replied: Yes, from 9:00 to 13:00." tag="Answered" />
        <MockRow icon="mail" title="Gmail · Question about an invoice" sub="Task created for the team" tag="Task" />
      </>
    ),
  },
  {
    id: "appointments",
    label: "Appointments",
    icon: "calendar",
    title: "Bookings made during the conversation",
    text: "Connect Google Calendar and Yelvo offers free times, books the slot and confirms it while the caller is still on the line.",
    bullets: ["Checks your calendar for free times", "Books the appointment in the call", "Sends a confirmation email"],
    mock: (
      <div className="uc-slots">
        {["09:00", "10:30", "14:30", "16:00"].map((slot) => (
          <span key={slot} className="uc-slot" data-picked={slot === "14:30"}>
            {slot}
          </span>
        ))}
        <MockRow icon="calendar" title="Friday 14:30 booked" sub="Confirmation sent by email" tag="Booked" />
      </div>
    ),
  },
  {
    id: "order-intake",
    label: "Order intake",
    icon: "bag",
    title: "Orders and requests, written down for you",
    text: "Yelvo asks for the details you need, in the order you choose, and passes them on in a structured form.",
    bullets: ["Collects the fields you define", "Appends a row to Google Sheets", "Sends a webhook to your own system"],
    mock: (
      <div className="uc-sheet">
        <div className="uc-sheet-head">
          <span>Name</span>
          <span>Item</span>
          <span>Pick-up</span>
        </div>
        <div className="uc-sheet-row">
          <span>J. Rivera</span>
          <span>2 × large</span>
          <span>Sat 11:00</span>
        </div>
        <div className="uc-sheet-row uc-sheet-row--new">
          <span>M. Chen</span>
          <span>1 × medium</span>
          <span>Sat 12:30</span>
        </div>
      </div>
    ),
  },
  {
    id: "after-hours",
    label: "After hours",
    icon: "moon",
    title: "Covered in the evening and at weekends",
    text: "Outside opening hours Yelvo still answers, takes the details and flags anything urgent according to your rules.",
    bullets: ["Answers around the clock", "Urgency rules flag what can't wait", "Your team picks up the tasks in the morning"],
    mock: (
      <>
        <MockRow icon="alert" title="Urgent · Call back first thing" sub="Matched your urgency rule" tag="Urgent" />
        <MockRow icon="task" title="2 new tasks" sub="Waiting for the team" />
      </>
    ),
  },
];

export function UseCaseTabs() {
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [indicator, setIndicator] = useState<{ left: number; width: number } | null>(null);

  useIsoLayoutEffect(() => {
    const measure = () => {
      const tab = tabRefs.current[active];
      if (tab) setIndicator({ left: tab.offsetLeft, width: tab.offsetWidth });
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [active]);

  const select = (index: number, focus = false) => {
    setActive(index);
    const tab = tabRefs.current[index];
    if (focus) tab?.focus();
    tab?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  };

  const onKeyDown = (event: KeyboardEvent) => {
    const last = USE_CASES.length - 1;
    const target =
      event.key === "ArrowRight" ? (active === last ? 0 : active + 1)
      : event.key === "ArrowLeft" ? (active === 0 ? last : active - 1)
      : event.key === "Home" ? 0
      : event.key === "End" ? last
      : null;
    if (target === null) return;
    event.preventDefault();
    select(target, true);
  };

  const current = USE_CASES[active];

  return (
    <div className="uc">
      <div className="uc-tabs-scroll">
        <div className="uc-tabs" role="tablist" aria-label="Use cases" ref={listRef} onKeyDown={onKeyDown}>
          {indicator && <span className="uc-indicator" style={{ transform: `translateX(${indicator.left}px)`, width: indicator.width }} aria-hidden="true" />}
          {USE_CASES.map((useCase, index) => (
            <button
              key={useCase.id}
              ref={(node) => {
                tabRefs.current[index] = node;
              }}
              id={`uc-tab-${useCase.id}`}
              type="button"
              role="tab"
              className="uc-tab"
              aria-selected={index === active}
              aria-controls={`uc-panel-${useCase.id}`}
              tabIndex={index === active ? 0 : -1}
              data-measured={indicator !== null}
              onClick={() => select(index)}
            >
              <Icon name={useCase.icon} width={16} height={16} />
              {useCase.label}
            </button>
          ))}
        </div>
      </div>
      <div
        key={current.id}
        className="uc-panel"
        role="tabpanel"
        id={`uc-panel-${current.id}`}
        aria-labelledby={`uc-tab-${current.id}`}
        tabIndex={0}
      >
        <div className="uc-copy">
          <h3 className="h3">{current.title}</h3>
          <p>{current.text}</p>
          <ul className="uc-bullets">
            {current.bullets.map((bullet) => (
              <li key={bullet}>
                <Icon name="check" width={16} height={16} />
                {bullet}
              </li>
            ))}
          </ul>
        </div>
        <div className="uc-mock" aria-hidden="true">
          <div className="uc-mock-card">{current.mock}</div>
        </div>
      </div>
    </div>
  );
}

export function UseCases() {
  return (
    <section className="section section--alt" id="use-cases" aria-labelledby="use-cases-title">
      <div className="container">
        <Reveal className="section-head section-head--split">
          <div>
            <p className="kicker">Use cases</p>
            <h2 id="use-cases-title" className="h2">
              One assistant, many jobs
            </h2>
            <p className="lead">Whatever your business does, Yelvo takes the conversations your team doesn't have time for.</p>
          </div>
          <Button variant="cta" size="lg" href={appLink("/signup")} icon="arrow">
            Get started
          </Button>
        </Reveal>
        <Reveal>
          <UseCaseTabs />
        </Reveal>
      </div>
    </section>
  );
}
