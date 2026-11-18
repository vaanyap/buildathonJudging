import { FormEvent, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

const DEMOS = [
  { label: "Organizer demo", email: "organizer@tech.nyu.edu", password: "organizer-demo" },
  { label: "Judge demo (Maya)", email: "maya.chen@nyu.edu", password: "judge-demo" },
];

export default function Login() {
  const { login, user } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (user) {
    return <Navigate to={user.role === "organizer" ? "/organizer" : "/judge"} replace />;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const signedIn = await login(email.trim(), password);
      navigate(signedIn.role === "organizer" ? "/organizer" : "/judge", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  async function useDemo(demo: (typeof DEMOS)[number]) {
    setEmail(demo.email);
    setPassword(demo.password);
    setBusy(true);
    setError("");
    try {
      const signedIn = await login(demo.email, demo.password);
      navigate(signedIn.role === "organizer" ? "/organizer" : "/judge", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <p className="wordmark">Tech@NYU</p>
        <h1>Buildathon judging</h1>
        <p className="lede">
          Judges score assigned projects from a phone. Organizers run the schedule, assignments, and results.
        </p>
        <label>
          Email
          <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="username" required />
        </label>
        <label>
          Password
          <input
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            autoComplete="current-password"
            required
          />
        </label>
        {error && <p className="banner bad">{error}</p>}
        <button className="btn primary" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <div className="demo-row">
          {DEMOS.map((demo) => (
            <button key={demo.email} className="btn ghost" type="button" disabled={busy} onClick={() => void useDemo(demo)}>
              {demo.label}
            </button>
          ))}
        </div>
        <p className="hint">Every judge demo account uses the password judge-demo. Avery Shah is the organizer.</p>
      </form>
    </main>
  );
}
