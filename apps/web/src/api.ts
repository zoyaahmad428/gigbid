// Thin client for apps/api. The session token lives in localStorage; the server stores only its hash.

const TOKEN_KEY = "gigbid.token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode: the session lasts for this tab only */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const tok = getToken();
  if (tok) headers.authorization = `Bearer ${tok}`;
  if (opts.body !== undefined) headers["content-type"] = "application/json";
  const res = await fetch(`/api${path}`, {
    method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = (data as any).detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x: any) => x.msg).join("; ") : res.statusText;
    if (res.status === 401) setToken(null);
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

export const enc = encodeURIComponent;

export function streamUrl(threadId: string, after = 0) {
  return `/api/runs/${enc(threadId)}/stream?after=${after}&token=${enc(getToken() ?? "")}`;
}

// ---- shapes returned by the API (only the fields the screens use)
export interface User {
  user_id: string;
  email: string;
  onboarded: boolean;
  settings: Partial<Settings>;
}

export interface Settings {
  persona: "ai_ml" | "fullstack" | "data_analyst";
  name: string;
  headline: string;
  hourly_rate: number;
  min_fixed_budget: number;
  marketplace_token: string;
  autonomy_auto_skip: boolean;
  timezone: string;
  weekly_connects_budget: number;
}

export interface Persona {
  persona: Settings["persona"];
  name: string;
  headline: string;
  category: string;
  skills: string[];
  does_not_do: string[];
  hourly_rate: number;
  min_fixed_budget: number;
  weekly_connects_budget: number;
  default_token: string;
}

export interface Run {
  thread_id: string;
  kind: "bid" | "convo";
  job_id: string | null;
  proposal_id: string | null;
  title: string | null;
  status: string;
  reason: string | null;
  created_at: number;
  updated_at: number;
  summary: Record<string, any>;
}

export interface Job {
  job_id: string;
  title: string;
  description: string;
  category: string;
  skills: string[];
  budget: { type: "fixed" | "hourly"; min: number | null; max: number | null };
  posted_at: string;
  client_id: string;
  connects_cost: number;
  screening_questions?: string[];
  proposals_count?: number;
  run?: Run | null;
  [k: string]: any;
}

export interface RunEvent {
  seq: number;
  ts: number;
  type: "started" | "node" | "interrupt" | "done" | "error" | "custom";
  node?: string;
  status?: string | null;
  reason?: string | null;
  payload?: any;
  message?: string;
  trace?: { tokens_in: number; tokens_out: number; cost_usd: number; events: number };
}
