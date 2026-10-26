import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/errors";
import { useAuth } from "../auth/AuthProvider";
import mark from "../assets/brand/yelvo-mark.svg";
import wordmark from "../assets/brand/yelvo-wordmark.svg";
import { SITE_URL } from "../lib/site";
import { businessNameFromWebsite, normalizeWebsiteUrl } from "../lib/url";
import styles from "./AuthPages.module.css";

function safeNext(value: string | null): string {
  return value?.startsWith("/") && !value.startsWith("//") ? value : "/inbox";
}

function AuthShell({ variant, children }: { variant: "login" | "signup"; children: ReactNode }) {
  return <div className={styles.shell}>
    <aside className={styles.panel} aria-label={variant === "signup" ? "Getting started" : "Welcome"}>
      <img className={styles.mark} src={mark} alt="" />
      <div className={styles.panelCopy}>
        <h2 className={styles.panelHeadline}>
          {variant === "signup" ? "Your assistant, ready to test in minutes" : "Welcome back"}
        </h2>
        {variant === "signup"
          ? <ol className={styles.steps}>
            <li><span className={styles.stepNumber}>01</span><span>Tell us about your business</span></li>
            <li><span className={styles.stepNumber}>02</span><span>We read your website and draft your assistant</span></li>
            <li><span className={styles.stepNumber}>03</span><span>Test-call it, then connect your number</span></li>
          </ol>
          : <p className={styles.panelIntro}>Your assistants and conversations are ready when you are.</p>}
      </div>
    </aside>
    <main id="main-content" className={styles.formRegion}>
      <div className={styles.card}>
        <p className={styles.brand}><img src={wordmark} alt="Yelvo" /></p>
        {children}
      </div>
    </main>
  </div>;
}

export function LoginPage() {
  const { login } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(email, password);
      navigate(safeNext(new URLSearchParams(location.search).get("next")), { replace: true });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to sign in.");
    } finally {
      setBusy(false);
    }
  }

  return <AuthShell variant="login">
    <h1>Sign in</h1>
    <p className={styles.intro}>Use your account to review conversations and manage your assistants.</p>
    <form className={`stack ${styles.form}`} onSubmit={submit}>
      {error && <p role="alert" className={styles.formError}>{error}</p>}
      <div className="field">
        <label htmlFor="email">Email address</label>
        <input id="email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="password">Password</label>
        <input id="password" type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
      </div>
      <div className={styles.actions}>
        <button className="button-primary" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </div>
    </form>
    <p className={styles.accountLink}>New here? <Link to="/signup">Create an account</Link></p>
  </AuthShell>;
}

type SignupForm = {
  email: string;
  password: string;
  display_name: string;
  tenant_name: string;
  website: string;
};

export function SignupPage() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const emailRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState<"account" | "business">("account");
  const [form, setForm] = useState<SignupForm>(() => {
    const website = new URLSearchParams(location.search).get("website") ?? "";
    return {
      email: "",
      password: "",
      display_name: "",
      tenant_name: businessNameFromWebsite(website),
      website,
    };
  });
  const [error, setError] = useState("");
  const [showSignInLink, setShowSignInLink] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (step === "account") emailRef.current?.focus();
    else nameRef.current?.focus();
  }, [step]);

  function clearError() {
    setError("");
    setShowSignInLink(false);
  }

  function change(key: keyof SignupForm, value: string) {
    setForm((current) => ({ ...current, [key]: value }));
    clearError();
  }

  function continueToBusiness(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    clearError();
    const email = form.email.trim();
    if (!email) {
      setError("Enter your email address.");
      return;
    }
    if (!emailRef.current?.validity.valid) {
      setError("Enter a valid email address.");
      return;
    }
    if (form.password.length < 12) {
      setError("Password must contain at least 12 characters.");
      return;
    }
    setForm((current) => ({ ...current, email }));
    setStep("business");
  }

  async function createAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    clearError();
    if (!form.display_name.trim()) {
      setError("Enter your name.");
      return;
    }
    if (!form.tenant_name.trim()) {
      setError("Enter your business name.");
      return;
    }

    setBusy(true);
    try {
      await signup({
        email: form.email.trim(),
        password: form.password,
        display_name: form.display_name.trim(),
        tenant_name: form.tenant_name.trim(),
      });
      const next = new URLSearchParams(location.search).get("next");
      const website = normalizeWebsiteUrl(form.website);
      const destination = next
        ? safeNext(next)
        : website
          ? `/assistants/new?website=${encodeURIComponent(website)}`
          : "/assistants/new";
      navigate(destination, { replace: true });
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setStep("account");
        setError("An account with this email already exists.");
        setShowSignInLink(true);
      } else {
        setError(caught instanceof ApiError ? caught.message : "Unable to create account.");
      }
    } finally {
      setBusy(false);
    }
  }

  const privacyUrl = `${SITE_URL.replace(/\/+$/, "")}/privacy`;

  return <AuthShell variant="signup">
    <p className={styles.stepCount}>Step {step === "account" ? "1" : "2"} of 2</p>
    <h1>{step === "account" ? "Create your account" : "About your business"}</h1>
    <form className={`stack ${styles.form}`} noValidate onSubmit={step === "account" ? continueToBusiness : createAccount}>
      {error && <p role="alert" className={styles.formError}>
        {error}{showSignInLink && <> <Link to="/login">Sign in instead</Link></>}
      </p>}
      {step === "account" ? <>
        <div className="field">
          <label htmlFor="signup-email">Email address</label>
          <input ref={emailRef} id="signup-email" type="email" autoComplete="email" required value={form.email} onChange={(event) => change("email", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="signup-password">Password</label>
          <div className={styles.passwordField}>
            <input id="signup-password" type={showPassword ? "text" : "password"} minLength={12} autoComplete="new-password" required value={form.password} onChange={(event) => change("password", event.target.value)} />
            <button className={styles.passwordToggle} type="button" aria-label={showPassword ? "Hide password" : "Show password"} aria-pressed={showPassword} onClick={() => setShowPassword((visible) => !visible)}>
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>
          <span className="help">Use at least 12 characters.</span>
        </div>
        <div className={styles.actions}>
          <button className="button-primary" type="submit">Continue</button>
        </div>
      </> : <>
        <div className="field">
          <label htmlFor="signup-name">Your name</label>
          <input ref={nameRef} id="signup-name" autoComplete="name" required value={form.display_name} onChange={(event) => change("display_name", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="signup-business-name">Business name</label>
          <input id="signup-business-name" required value={form.tenant_name} onChange={(event) => change("tenant_name", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="signup-website">Website (optional)</label>
          <input id="signup-website" type="text" inputMode="url" autoComplete="url" placeholder="example.co.uk" value={form.website} onChange={(event) => change("website", event.target.value)} />
        </div>
        <div className={`${styles.actions} ${styles.businessActions}`}>
          <button className="button-secondary" type="button" onClick={() => { clearError(); setStep("account"); }}>Back</button>
          <button className="button-primary" type="submit" disabled={busy}>{busy ? "Creating account…" : "Create account"}</button>
        </div>
      </>}
    </form>
    {step === "business" && <p className={styles.terms}>By creating an account you agree to our <a href={privacyUrl}>privacy policy</a>.</p>}
    <p className={styles.accountLink}>Already registered? <Link to="/login">Sign in</Link></p>
  </AuthShell>;
}
