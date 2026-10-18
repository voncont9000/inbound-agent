import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/errors";
import { useAuth } from "../auth/AuthProvider";

function safeNext(value: string | null): string {
  return value?.startsWith("/") && !value.startsWith("//") ? value : "/inbox";
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

  return <main id="main-content" className="auth-card">
    <p className="eyebrow">Operator workspace</p>
    <h1>Sign in</h1>
    <p className="intro">Use your account to review conversations and manage your assistants.</p>
    <form className="stack" onSubmit={submit}>
      {error && <p role="alert" className="state-error">{error}</p>}
      <div className="field"><label htmlFor="email">Email address</label><input id="email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></div>
      <div className="field"><label htmlFor="password">Password</label><input id="password" type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} /></div>
      <button className="button-primary" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
    <p className="muted">New here? <Link to="/signup">Create an account</Link></p>
  </main>;
}

export function SignupPage() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [form, setForm] = useState({ email: "", password: "", display_name: "", tenant_name: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  function change(key: keyof typeof form, value: string) {
    setForm((current) => ({ ...current, [key]: value }));
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (form.password.length < 12) { setError("Password must contain at least 12 characters."); return; }
    setBusy(true);
    setError("");
    try {
      await signup(form);
      navigate(safeNext(new URLSearchParams(location.search).get("next")), { replace: true });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to create account.");
    } finally {
      setBusy(false);
    }
  }
  return <main id="main-content" className="auth-card">
    <p className="eyebrow">Operator workspace</p>
    <h1>Create your account</h1>
    <form className="stack" onSubmit={submit}>
      {error && <p role="alert" className="state-error">{error}</p>}
      <div className="field"><label htmlFor="signup-email">Email address</label><input id="signup-email" type="email" autoComplete="email" required value={form.email} onChange={(event) => change("email", event.target.value)} /></div>
      <div className="field"><label htmlFor="signup-password">Password</label><input id="signup-password" type="password" minLength={12} autoComplete="new-password" required value={form.password} onChange={(event) => change("password", event.target.value)} /><span className="help">Use at least 12 characters.</span></div>
      <div className="field"><label htmlFor="display-name">Your name</label><input id="display-name" required value={form.display_name} onChange={(event) => change("display_name", event.target.value)} /></div>
      <div className="field"><label htmlFor="tenant-name">Workspace name</label><input id="tenant-name" required value={form.tenant_name} onChange={(event) => change("tenant_name", event.target.value)} /></div>
      <button className="button-primary" type="submit" disabled={busy}>{busy ? "Creating account…" : "Create account"}</button>
    </form>
    <p className="muted">Already registered? <Link to="/login">Sign in</Link></p>
  </main>;
}
