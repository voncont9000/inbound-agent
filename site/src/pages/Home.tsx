import { Layout } from "../components/Layout";
import { Reveal } from "../components/Reveal";
import { CallDemo } from "../sections/CallDemo";
import { Faq } from "../sections/Faq";
import { Features } from "../sections/Features";
import { FinalCta } from "../sections/FinalCta";
import { Hero } from "../sections/Hero";
import { Integrations } from "../sections/Integrations";
import { Steps } from "../sections/Steps";
import { UseCases } from "../sections/UseCases";

export default function Home() {
  return (
    <Layout onHome>
      <Hero />
      <section className="section section--demo" id="product" aria-labelledby="demo-title">
        <div className="container">
          <Reveal className="section-head">
            <p className="kicker">See it work</p>
            <h2 id="demo-title" className="h2">
              It listens, answers and writes it all down
            </h2>
            <p className="lead">
              While the caller talks, Yelvo fills in their details, creates the follow-up task and emails your team.
            </p>
          </Reveal>
          <Reveal>
            <CallDemo />
          </Reveal>
        </div>
      </section>
      <Steps />
      <UseCases />
      <Features />
      <Integrations />
      <Faq />
      <FinalCta />
    </Layout>
  );
}
