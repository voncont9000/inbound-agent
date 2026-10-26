import { Icon } from "../components/Icon";
import { Reveal } from "../components/Reveal";
import { StepItem } from "../components/ui";

function WebsiteCard() {
  return (
    <div className="mini mini--website" aria-hidden="true">
      <div className="mini-url">
        <Icon name="globe" width={14} height={14} />
        yourbusiness.com
      </div>
      <div className="mini-progress">
        <span />
      </div>
      <ul className="mini-checks">
        {["Opening hours", "Services", "Common questions"].map((item, index) => (
          <li key={item} style={{ animationDelay: `${600 + index * 450}ms` }}>
            <Icon name="check" width={14} height={14} />
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function BehaviourCard() {
  return (
    <div className="mini" aria-hidden="true">
      <p className="mini-label">Collect</p>
      <div className="mini-chips">
        <span>Name</span>
        <span>Phone</span>
        <span>Reason</span>
      </div>
      <p className="mini-label">After the conversation</p>
      <ul className="mini-toggles">
        {[
          ["mail", "Email the team"],
          ["sheet", "Add a row to Google Sheets"],
          ["webhook", "Send a webhook"],
          ["task", "Create a task"],
        ].map(([icon, label], index) => (
          <li key={label} style={{ animationDelay: `${index * 160}ms` }}>
            <Icon name={icon as "mail"} width={14} height={14} />
            {label}
            <span className="mini-switch" />
          </li>
        ))}
      </ul>
    </div>
  );
}

function ChannelsCard() {
  return (
    <ul className="mini mini-channels" aria-hidden="true">
      {[
        ["phone", "Phone number"],
        ["mail", "Gmail"],
        ["chat", "WhatsApp"],
      ].map(([icon, label], index) => (
        <li key={label} style={{ animationDelay: `${400 + index * 350}ms` }}>
          <span className="mini-channel-icon">
            <Icon name={icon as "mail"} width={16} height={16} />
          </span>
          {label}
          <span className="mini-connected">
            <Icon name="check" width={12} height={12} />
            Connected
          </span>
        </li>
      ))}
    </ul>
  );
}

const STEPS = [
  {
    title: "Create your assistant from your website",
    text: "Enter your website and Yelvo drafts an assistant with your services, opening hours and answers to common questions.",
    card: <WebsiteCard />,
  },
  {
    title: "Decide what it says and does",
    text: "Set the greeting, the details it collects, and what happens after each conversation: an email, a Google Sheets row, a webhook or a task.",
    card: <BehaviourCard />,
  },
  {
    title: "Connect your channels",
    text: "Point your phone number at Yelvo and connect Gmail and WhatsApp. Every conversation lands in one inbox.",
    card: <ChannelsCard />,
  },
];

export function Steps() {
  return (
    <section className="section" id="how-it-works" aria-labelledby="steps-title">
      <div className="container">
        <Reveal className="section-head">
          <p className="kicker">How it works</p>
          <h2 id="steps-title" className="h2">
            Live in minutes
          </h2>
          <p className="lead">Set up takes a website address and a few choices. No code, no scripts to write.</p>
        </Reveal>
        <ol className="steps">
          {STEPS.map((step, index) => (
            <Reveal as="li" key={step.title} className="step-card" delay={index * 140}>
              <StepItem number={index + 1} title={step.title}>
                {step.text}
              </StepItem>
              <div className="step-visual">{step.card}</div>
            </Reveal>
          ))}
        </ol>
      </div>
    </section>
  );
}
