import { useEffect, useState } from "react";
import { ApiError } from "./api";

// ---- formatting ----

/** 0.0123 -> "+1.23%" */
export function pct(x: number, digits = 2, signed = true): string {
  const s = (x * 100).toFixed(digits);
  return signed && x > 0 ? `+${s}%` : `${s}%`;
}

/** 0.0123 -> "+1.2 pts" (percentage points, for contributions to a period return) */
export function pts(x: number, digits = 1): string {
  const s = (x * 100).toFixed(digits);
  return `${x > 0 ? "+" : ""}${s} pts`;
}

export function num(x: number, digits = 2): string {
  return x.toFixed(digits);
}

/** 1234567 -> "$1.2M" */
export function money(x: number): string {
  const abs = Math.abs(x);
  if (abs >= 1e9) return `$${(x / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(x / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(x / 1e3).toFixed(0)}K`;
  return `$${x.toFixed(0)}`;
}

export function pValue(p: number): string {
  if (p < 0.001) return "< 0.001";
  return p.toFixed(3);
}

/** "p < 0.001" or "p = 0.042", for use in sentences. */
export function pText(p: number): string {
  return p < 0.001 ? "p < 0.001" : `p = ${p.toFixed(3)}`;
}

const DATE_FMT = new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric" });
const MONTH_FMT = new Intl.DateTimeFormat("en-US", { year: "2-digit", month: "short" });

export function fmtDate(iso: string): string {
  return DATE_FMT.format(new Date(`${iso}T00:00:00`));
}

export function fmtMonth(iso: string): string {
  return MONTH_FMT.format(new Date(`${iso}T00:00:00`));
}

/** Plain words for an empirical return period, p = (1 + exceedances) / (history + 1).
 * With at most 250 days of history, a period above 126 means nothing in the history was as
 * large, so the honest statement is "bigger than anything in the last N days". */
export function rarity(period: number): string {
  if (period > 126) {
    return `It is bigger than any unexplained move in the previous ${Math.round(period - 1)} trading days.`;
  }
  return `A move this size happens about once every ${Math.round(period)} trading days.`;
}

export function titleCase(name: string): string {
  return name.toLowerCase().replace(/\b([a-z])/g, (c) => c.toUpperCase());
}

// ---- data loading ----

export type Async<T> =
  | { state: "loading"; waking?: boolean }
  | { state: "error"; error: Error }
  | { state: "ready"; data: T };

const RETRY_DELAYS_MS = [3000, 5000, 8000, 10000, 15000, 20000];

/** True for errors that go away on their own: a sleeping free-tier server, or data still loading. */
function transient(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 0 || error.status === 503 || error.status === 502);
}

/** Runs `load` whenever `key` changes and aborts the previous request. Transient failures are
 * retried for about a minute, with `waking` set so the page can say what is happening. */
export function useAsync<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>): Async<T> {
  const [result, setResult] = useState<{ key: string | null; value: Async<T> }>({
    key: null,
    value: { state: "loading" },
  });

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const attempt = (n: number) => {
      load(controller.signal).then(
        (data) => setResult({ key, value: { state: "ready", data } }),
        (error: unknown) => {
          if (controller.signal.aborted) return;
          if (transient(error) && n < RETRY_DELAYS_MS.length) {
            setResult({ key, value: { state: "loading", waking: true } });
            timer = window.setTimeout(() => attempt(n + 1), RETRY_DELAYS_MS[n]);
            return;
          }
          setResult({ key, value: { state: "error", error: error instanceof Error ? error : new Error(String(error)) } });
        },
      );
    };
    attempt(0);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
    // `load` is expected to depend only on `key`
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return result.key === key ? result.value : { state: "loading" };
}

// ---- URL state (shareable links: ?t=NVDA&d=2026-10-30) ----

export interface Route {
  ticker: string | null;
  day: string | null;
}

export function readRoute(): Route {
  const params = new URLSearchParams(window.location.search);
  return { ticker: params.get("t")?.toUpperCase() ?? null, day: params.get("d") };
}

export function writeRoute(route: Route, replace = false): void {
  const params = new URLSearchParams();
  if (route.ticker) params.set("t", route.ticker);
  if (route.ticker && route.day) params.set("d", route.day);
  const query = params.toString();
  const url = query ? `?${query}` : window.location.pathname;
  if (replace) window.history.replaceState(null, "", url);
  else window.history.pushState(null, "", url);
}
