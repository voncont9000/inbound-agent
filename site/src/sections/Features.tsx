import { Icon, type IconName } from "../components/Icon";
import { Reveal } from "../components/Reveal";

const FEATURES: { icon: IconName; title: string; text: string }[] = [
  { icon: "phone", title: "Answers phone calls", text: "On your Twilio number, plus test calls straight from the browser." },
  { icon: "chat", title: "Replies on Gmail and WhatsApp", text: "The same assistant handles your inbox and your WhatsApp messages." },
  { icon: "inbox", title: "One inbox", text: "Every conversation in one place, with transcripts and call recordings." },
  { icon: "users", title: "Contacts and history", text: "Each caller's history in one view, and returning callers are recognised." },
  { icon: "task", title: "Tasks for your team", text: "Follow-ups land as tasks, so nothing depends on someone's memory." },
  { icon: "send", title: "Actions after each conversation", text: "Templated emails, Google Sheets rows and webhooks, sent automatically." },
  { icon: "calendar", title: "Calendar booking", text: "Finds a free time and books the appointment during the conversation." },
  { icon: "forward", title: "Forwarding to a person", text: "Hands the call to a colleague when the caller needs a human." },
  { icon: "book", title: "Knowledge and FAQs", text: "Answers from the facts and FAQs you give it, in your own words." },
  { icon: "languages", title: "Languages and voices", text: "Speaks multiple languages, with a choice of voices." },
  { icon: "alert", title: "Urgency rules", text: "You decide what counts as urgent, and urgent requests are flagged." },
];

export function Features() {
  return (
    <section className="section" id="features" aria-labelledby="features-title">
      <div className="container">
        <Reveal className="section-head">
          <p className="kicker">Features</p>
          <h2 id="features-title" className="h2">
            Everything a good receptionist does
          </h2>
          <p className="lead">Yelvo answers, takes the details down and makes sure the right person follows up.</p>
        </Reveal>
        <ul className="features">
          {FEATURES.map((feature, index) => (
            <Reveal as="li" key={feature.title} className="feature" delay={(index % 3) * 90}>
              <span className="feature-icon">
                <Icon name={feature.icon} />
              </span>
              <h3 className="h4">{feature.title}</h3>
              <p>{feature.text}</p>
            </Reveal>
          ))}
        </ul>
      </div>
    </section>
  );
}
