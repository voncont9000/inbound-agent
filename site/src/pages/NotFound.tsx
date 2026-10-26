import { Layout } from "../components/Layout";
import { Button, SoundBars } from "../components/ui";

export default function NotFound() {
  return (
    <Layout>
      <section className="not-found container">
        <SoundBars className="sound-bars--xl" />
        <p className="kicker">404</p>
        <h1 className="h1">We couldn't find that page</h1>
        <p className="lead">The link may be old, or the page may have moved.</p>
        <Button variant="cta" size="lg" href="/" icon="arrow">
          Back to the home page
        </Button>
      </section>
    </Layout>
  );
}
