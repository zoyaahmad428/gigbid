import { useCallback, useEffect, useRef, useState } from "react";
import { api, RunEvent, streamUrl } from "./api";

/** GET a path; returns data, error, loading and a reload function. Pass null to skip. */
export function useApi<T = any>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const seq = useRef(0);

  const load = useCallback(async () => {
    if (!path) return;
    const mine = ++seq.current;
    setLoading(true);
    try {
      const d = await api<T>(path);
      if (mine === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e: any) {
      if (mine === seq.current) setError(e.message);
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, ...deps]);

  return { data, error, loading, reload: load, setData };
}

/**
 * Follow a run's server-sent events. The server replays every stored event first, so after a refresh
 * the timeline is rebuilt from the start; EventSource reconnects with Last-Event-ID on its own.
 */
export function useRunStream(threadId: string | null, nonce: number, onFinal?: (status: string) => void) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const final = useRef(onFinal);
  final.current = onFinal;

  useEffect(() => {
    if (!threadId) return;
    setEvents([]);
    let last = 0;
    const es = new EventSource(streamUrl(threadId, 0));
    const add = (e: MessageEvent) => {
      const ev = JSON.parse(e.data) as RunEvent;
      if (ev.seq <= last) return;
      last = ev.seq;
      setEvents((xs) => [...xs, ev]);
    };
    for (const t of ["started", "node", "interrupt", "done", "error", "custom"]) es.addEventListener(t, add);
    es.addEventListener("end", (e) => {
      es.close();
      setConnected(false);
      final.current?.(JSON.parse((e as MessageEvent).data).status);
    });
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    return () => es.close();
  }, [threadId, nonce]);

  return { events, connected };
}

/** Re-run a callback every `ms` while `on` is true (for lists that change while runs progress). */
export function usePoll(fn: () => void, ms: number, on = true) {
  const f = useRef(fn);
  f.current = fn;
  useEffect(() => {
    if (!on) return;
    const id = setInterval(() => f.current(), ms);
    return () => clearInterval(id);
  }, [ms, on]);
}
