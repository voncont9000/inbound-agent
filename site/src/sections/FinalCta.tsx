import { Reveal } from "../components/Reveal";
import { Button, SoundBars } from "../components/ui";
import { appLink, DEMO_MAILTO } from "../config";

export function FinalCta() {
  return (
    <section className="section cta-section" aria-labelledby="cta-title">
      <div className="container">
        <Reveal className="cta-band">
          <SoundBars className="sound-bars--live sound-bars--xl cta-bars" />
          <h2 id="cta-title" className="h2">
            Let Yelvo pick up the next call
          </h2>
          <p className="lead">Draft your assistant from your website and hear it answer in a few minutes.</p>
          <div className="cta-actions">
            <Button variant="cta" size="lg" href={appLink("/signup")} icon="arrow">
              Get started
            </Button>
            <Button variant="outline" size="lg" href={DEMO_MAILTO}>
              Book a demo
            </Button>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
