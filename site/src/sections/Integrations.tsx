import { Icon, type IconName } from "../components/Icon";
import { Reveal } from "../components/Reveal";

const INTEGRATIONS: { icon: IconName; name: string; text: string }[] = [
  { icon: "mail", name: "Gmail", text: "Read and reply to email" },
  { icon: "sheet", name: "Google Sheets", text: "Add a row per conversation" },
  { icon: "calendar", name: "Google Calendar", text: "Book appointments" },
  { icon: "chat", name: "WhatsApp", text: "Answer messages" },
  { icon: "phone", name: "Twilio", text: "Your phone number" },
  { icon: "webhook", name: "Webhooks", text: "Send data to your systems" },
];

function Tile({ icon, name, text }: { icon: IconName; name: string; text: string }) {
  return (
    <li className="int-tile">
      <span className="int-icon">
        <Icon name={icon} />
      </span>
      <span>
        <span className="int-name">{name}</span>
        <span className="int-text">{text}</span>
      </span>
    </li>
  );
}

export function Integrations() {
  return (
    <section className="section section--alt" id="integrations" aria-labelledby="integrations-title">
      <div className="container">
        <Reveal className="section-head">
          <p className="kicker">Integrations</p>
          <h2 id="integrations-title" className="h2">
            Works with the tools you use
          </h2>
          <p className="lead">Connect your channels and send every conversation where your team already works.</p>
        </Reveal>
      </div>
      <Reveal className="marquee">
        <div className="marquee-track">
          <ul className="marquee-group">
            {INTEGRATIONS.map((item) => (
              <Tile key={item.name} {...item} />
            ))}
          </ul>
          <ul className="marquee-group" aria-hidden="true">
            {INTEGRATIONS.map((item) => (
              <Tile key={item.name} {...item} />
            ))}
          </ul>
        </div>
      </Reveal>
    </section>
  );
}
