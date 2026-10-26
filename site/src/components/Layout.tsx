import { useEffect, useState, type ReactNode } from "react";
import { appLink, DEMO_MAILTO } from "../config";
import { Button, Wordmark } from "./ui";
import { Icon } from "./Icon";

const NAV_ANCHORS = [
  { id: "product", label: "Product" },
  { id: "use-cases", label: "Use cases" },
  { id: "integrations", label: "Integrations" },
  { id: "faq", label: "FAQ" },
];

function anchorHref(id: string, onHome: boolean) {
  return onHome ? `#${id}` : `/#${id}`;
}

function Header({ onHome }: { onHome: boolean }) {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const update = () => setScrolled(window.scrollY > 8);
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, []);

  useEffect(() => {
    document.body.dataset.menuOpen = String(open);
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onResize = () => {
      if (window.innerWidth > 960) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    document.querySelector<HTMLElement>("#mobile-menu a")?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
    };
  }, [open]);

  const close = () => setOpen(false);

  return (
    <header className="site-header" data-scrolled={scrolled || open} data-open={open}>
      <div className="container site-header-row">
        <a className="site-logo" href="/" aria-label="Yelvo home">
          <Wordmark height={30} />
        </a>
        <nav className="site-nav" aria-label="Main">
          <ul>
            {NAV_ANCHORS.map((item) => (
              <li key={item.id}>
                <a href={anchorHref(item.id, onHome)}>{item.label}</a>
              </li>
            ))}
            <li>
              <a href={appLink("/help")}>Help</a>
            </li>
          </ul>
        </nav>
        <div className="site-actions">
          <Button variant="ghost" href={appLink("/login")}>
            Sign in
          </Button>
          <Button variant="outline" href={DEMO_MAILTO}>
            Book a demo
          </Button>
          <Button variant="cta" href={appLink("/signup")}>
            Get started
          </Button>
        </div>
        <button
          type="button"
          className="menu-toggle"
          aria-expanded={open}
          aria-controls="mobile-menu"
          aria-label={open ? "Close menu" : "Open menu"}
          onClick={() => setOpen((value) => !value)}
        >
          <Icon name={open ? "close" : "menu"} />
        </button>
      </div>
      <div id="mobile-menu" className="mobile-menu" hidden={!open}>
        <nav className="container" aria-label="Mobile">
          <ul className="mobile-menu-links">
            {NAV_ANCHORS.map((item) => (
              <li key={item.id}>
                <a href={anchorHref(item.id, onHome)} onClick={close}>
                  {item.label}
                </a>
              </li>
            ))}
            <li>
              <a href={appLink("/help")}>Help</a>
            </li>
          </ul>
          <div className="mobile-menu-actions">
            <Button variant="cta" size="lg" href={appLink("/signup")} icon="arrow">
              Get started
            </Button>
            <Button variant="outline" size="lg" href={DEMO_MAILTO}>
              Book a demo
            </Button>
            <Button variant="ghost" size="lg" href={appLink("/login")}>
              Sign in
            </Button>
          </div>
        </nav>
      </div>
    </header>
  );
}

function Footer({ onHome }: { onHome: boolean }) {
  return (
    <footer className="site-footer">
      <div className="container site-footer-grid">
        <div className="site-footer-brand">
          <Wordmark height={34} />
          <p>The AI receptionist that answers your calls, Gmail and WhatsApp.</p>
        </div>
        <nav aria-label="Product">
          <h2 className="site-footer-title">Product</h2>
          <ul>
            <li>
              <a href={anchorHref("product", onHome)}>Live demo</a>
            </li>
            <li>
              <a href={anchorHref("how-it-works", onHome)}>How it works</a>
            </li>
            <li>
              <a href={anchorHref("features", onHome)}>Features</a>
            </li>
            {NAV_ANCHORS.slice(1).map((item) => (
              <li key={item.id}>
                <a href={anchorHref(item.id, onHome)}>{item.label}</a>
              </li>
            ))}
          </ul>
        </nav>
        <nav aria-label="Account">
          <h2 className="site-footer-title">Yelvo</h2>
          <ul>
            <li>
              <a href={appLink("/help")}>Help</a>
            </li>
            <li>
              <a href={appLink("/login")}>Sign in</a>
            </li>
            <li>
              <a href={appLink("/signup")}>Get started</a>
            </li>
            <li>
              <a href={DEMO_MAILTO}>Book a demo</a>
            </li>
          </ul>
        </nav>
        <nav aria-label="Legal">
          <h2 className="site-footer-title">Legal</h2>
          <ul>
            <li>
              <a href="/privacy">Privacy</a>
            </li>
            <li>
              <a href="/imprint">Imprint</a>
            </li>
          </ul>
        </nav>
      </div>
      <div className="container site-footer-base">
        <p>© 2026 Yelvo</p>
      </div>
    </footer>
  );
}

export function Layout({ onHome = false, children }: { onHome?: boolean; children: ReactNode }) {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Header onHome={onHome} />
      <main id="main" tabIndex={-1}>
        {children}
      </main>
      <Footer onHome={onHome} />
    </>
  );
}
