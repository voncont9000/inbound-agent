import { Accordion, type AccordionItem } from "../components/Accordion";
import { Reveal } from "../components/Reveal";
import { DEMO_MAILTO } from "../config";

const FAQ_ITEMS: AccordionItem[] = [
  {
    question: "How long does setup take?",
    answer:
      "Enter your website and Yelvo drafts your assistant from it. You then review what it says, choose the details it collects and connect your channels. You can test it with a browser call before any customer hears it.",
  },
  {
    question: "Which channels does Yelvo answer?",
    answer:
      "Phone calls on a Twilio number, email in Gmail and messages on WhatsApp. All conversations land in the same inbox.",
  },
  {
    question: "Can it hand a caller over to a person?",
    answer:
      "Yes. You set the number to forward to, and the assistant puts the caller through when they ask for a person or when your rules say it should.",
  },
  {
    question: "What happens when it doesn't know the answer?",
    answer:
      "It tells the caller it doesn't know rather than guessing, takes their details and creates a task so someone on your team can follow up.",
  },
  {
    question: "Which languages does it speak?",
    answer: "Yelvo supports multiple languages and voices. You choose the language and voice your assistant uses.",
  },
  {
    question: "Are calls recorded?",
    answer:
      "Only if you turn recording on. When it is on, callers hear a recording notice at the start of the call, and the recording appears next to the transcript in your inbox.",
  },
];

export function Faq() {
  return (
    <section className="section" id="faq" aria-labelledby="faq-title">
      <div className="container faq">
        <Reveal className="faq-head">
          <p className="kicker">FAQ</p>
          <h2 id="faq-title" className="h2">
            Questions, answered
          </h2>
          <p className="lead">
            Something else on your mind? <a href={DEMO_MAILTO}>Email us</a> and we'll walk you through it.
          </p>
        </Reveal>
        <Reveal>
          <Accordion items={FAQ_ITEMS} />
        </Reveal>
      </div>
    </section>
  );
}
