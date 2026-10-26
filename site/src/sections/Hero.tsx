import { useState, type FormEvent } from "react";
import { appLink } from "../config";
import { HERO_HEADLINE } from "../content/hero";
import { signupUrl } from "../signup";
import { Icon } from "../components/Icon";
import { Button, Eyebrow, SoundBars } from "../components/ui";
import { LiveCallCard } from "./LiveCallCard";

export function HeroForm({ onNavigate = (url: string) => window.location.assign(url) }: { onNavigate?: (url: string) => void }) {
  const [website, setWebsite] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onNavigate(signupUrl(website));
  };
  return (
    <form className="hero-form" onSubmit={submit} action={appLink("/signup")} method="get">
      <div className="dt-url">
        <Icon name="globe" />
        <label className="sr-only" htmlFor="hero-website">
          Your website
        </label>
        <input
          id="hero-website"
          name="website"
          type="text"
          inputMode="url"
          autoComplete="url"
          placeholder="Enter your website"
          value={website}
          onChange={(event) => setWebsite(event.target.value)}
        />
        <Button variant="cta" size="lg" type="submit" icon="arrow">
          Get started
        </Button>
      </div>
      <ul className="dt-url-hints">
        <li>
          <Icon name="check" />
          We'll read it and draft your assistant
        </li>
        <li>
          <Icon name="check" />
          Test it in your browser
        </li>
      </ul>
    </form>
  );
}

export function Hero() {
  return (
    <section className="hero" aria-labelledby="hero-title">
      <div className="container hero-inner">
        <div className="hero-copy">
          <div className="hero-step" style={{ animationDelay: "0ms" }}>
            <Eyebrow>AI receptionist for calls, Gmail and WhatsApp</Eyebrow>
          </div>
          <h1 id="hero-title" className="display hero-step" style={{ animationDelay: "60ms" }}>
            {HERO_HEADLINE}
          </h1>
          <p className="lead hero-step" style={{ animationDelay: "120ms" }}>
            Yelvo picks up when your team can't, answers from what your business knows, and hands you the details and the
            follow-up.
          </p>
          <div className="hero-step" style={{ animationDelay: "180ms" }}>
            <HeroForm />
          </div>
        </div>
        <div className="hero-visual hero-step" style={{ animationDelay: "240ms" }}>
          <div className="hero-block">
            <div className="hero-mark" aria-hidden="true">
              <span className="hero-mark-ring" />
              <span className="hero-mark-ring hero-mark-ring--late" />
              <span className="hero-mark-dot">
                <SoundBars className="sound-bars--live sound-bars--xl" />
              </span>
            </div>
            <LiveCallCard />
            <div className="hero-chip hero-chip--a" aria-hidden="true">
              <Icon name="phone" /> Phone
            </div>
            <div className="hero-chip hero-chip--b" aria-hidden="true">
              <Icon name="mail" /> Gmail
            </div>
            <div className="hero-chip hero-chip--c" aria-hidden="true">
              <Icon name="chat" /> WhatsApp
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
