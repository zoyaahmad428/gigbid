import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api, getToken, setToken, User } from "./api";
import { usePoll } from "./hooks";
import { Spinner } from "./ui";
import Login from "./pages/Login";
import Onboarding from "./pages/Onboarding";
import Inbox from "./pages/Inbox";
import JobDetail from "./pages/JobDetail";
import RunPage from "./pages/Run";
import Pipeline from "./pages/Pipeline";
import Messages from "./pages/Messages";
import Analytics from "./pages/Analytics";
import SettingsPage from "./pages/Settings";
import Approvals from "./pages/Approvals";

interface AuthCtx {
  user: User | null;
  refresh: () => Promise<void>;
  signIn: (token: string, user: User) => void;
  signOut: () => void;
}

const Ctx = createContext<AuthCtx>(null as any);
export const useAuth = () => useContext(Ctx);

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      return;
    }
    try {
      setUser(await api<User>("/me"));
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setReady(true));
  }, [refresh]);

  const signIn = (token: string, u: User) => {
    setToken(token);
    setUser(u);
  };
  const signOut = () => {
    api("/auth/logout", { method: "POST" }).catch(() => {});
    setToken(null);
    setUser(null);
  };

  if (!ready) return <Spinner label="Starting" />;
  return (
    <Ctx.Provider value={{ user, refresh, signIn, signOut }}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
          <Route path="/onboarding" element={!user ? <Navigate to="/login" replace /> : <Onboarding />} />
          <Route path="/*" element={<Shell />} />
        </Routes>
      </BrowserRouter>
    </Ctx.Provider>
  );
}

const NAV = [
  ["/", "Inbox"],
  ["/approvals", "Approvals"],
  ["/pipeline", "Pipeline"],
  ["/messages", "Messages"],
  ["/runs", "Agent runs"],
  ["/analytics", "Analytics"],
  ["/settings", "Settings"],
] as const;

function Shell() {
  const { user, signOut } = useAuth();
  const loc = useLocation();
  const [pending, setPending] = useState(0);
  const [connects, setConnects] = useState<number | null>(null);
  const [menu, setMenu] = useState(false);

  const poll = useCallback(() => {
    if (!user?.onboarded) return;
    api("/approvals").then((d) => setPending(d.runs.length)).catch(() => {});
    api("/account").then((d) => setConnects(d.connects_balance)).catch(() => {});
  }, [user?.onboarded]);
  useEffect(poll, [poll, loc.pathname]);
  useEffect(() => {                                  // a run finished or a decision was sent
    window.addEventListener("gigbid:changed", poll);
    return () => window.removeEventListener("gigbid:changed", poll);
  }, [poll]);
  usePoll(poll, 10000, !!user?.onboarded);
  useEffect(() => setMenu(false), [loc.pathname]);

  if (!user) return <Navigate to="/login" replace />;
  if (!user.onboarded) return <Navigate to="/onboarding" replace />;

  return (
    <div className="shell">
      <aside className={`side ${menu ? "open" : ""}`}>
        <div className="brand">
          <span className="logo">G</span> GigBid
        </div>
        <nav>
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
              {label}
              {to === "/approvals" && pending > 0 && <span className="count">{pending}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          <div className="who">
            <strong>{user.settings.name}</strong>
            <span>{user.email}</span>
          </div>
          <button className="link" onClick={signOut}>Sign out</button>
        </div>
      </aside>
      <div className="main">
        <header className="top">
          <button className="menu-btn" aria-label="Menu" onClick={() => setMenu((m) => !m)}>☰</button>
          <div className="grow" />
          {pending > 0 && (
            <NavLink to="/approvals" className="pill warn">{pending} waiting for approval</NavLink>
          )}
          <span className="pill" title="Connects balance on the marketplace">
            {connects ?? "…"} Connects
          </span>
        </header>
        <main className="content">
          <Routes>
            <Route path="/" element={<Inbox />} />
            <Route path="/jobs/:jobId" element={<JobDetail />} />
            <Route path="/approvals" element={<Approvals />} />
            <Route path="/runs" element={<RunsList />} />
            <Route path="/runs/:threadId" element={<RunPage />} />
            <Route path="/pipeline" element={<Pipeline />} />
            <Route path="/messages" element={<Messages />} />
            <Route path="/messages/:proposalId" element={<Messages />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function RunsList() {
  return <Approvals all />;
}
