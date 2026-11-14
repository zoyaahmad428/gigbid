import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, User } from "../api";
import { useAuth } from "../App";
import { ErrorNote } from "../ui";

export default function Login() {
  const { signIn } = useAuth();
  const nav = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ token: string; user: User }>(`/auth/${mode}`, { body: { email, password } });
      signIn(r.token, r.user);
      nav(r.user.onboarded ? "/" : "/onboarding");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <form className="card auth-card" onSubmit={submit}>
        <div className="brand big">
          <span className="logo">G</span> GigBid
        </div>
        <p className="muted">
          Finds jobs that fit your portfolio, drafts proposals backed by your own evidence, and sends nothing
          until you approve it.
        </p>
        <label>
          Email
          <input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Password
          <input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={8}
                 value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        <ErrorNote error={error} />
        <button className="primary" disabled={busy}>{mode === "login" ? "Sign in" : "Create account"}</button>
        <button type="button" className="link" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "New here? Create an account" : "Have an account? Sign in"}
        </button>
      </form>
    </div>
  );
}
