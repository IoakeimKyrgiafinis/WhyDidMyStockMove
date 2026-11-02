// Typed client for the backend's /api/v1. Types mirror backend/src/whymoved/api/schemas.py.

const API_URL = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type HealthStatus = "good" | "caution" | "poor";

export interface Meta {
  status: "ready" | "loading" | "unavailable";
  message: string | null;
  as_of: string | null;
  n_stocks: number;
  window: number;
  alpha: number;
}

export interface StockRef {
  ticker: string;
  name: string;
  sector: string;
  sector_etf: string;
}

export interface Estimate {
  estimate: number;
  se: number;
}

export interface Interval {
  estimate: number;
  low: number;
  high: number;
}

export interface Profile {
  stock: StockRef;
  last_close: number;
  last_date: string;
  first_date: string;
  n_returns: number;
  window: number;
  beta_market: Estimate;
  beta_sector: Estimate;
  r_squared: number;
  health: {
    status: HealthStatus;
    reasons: string[];
    r_squared: number;
    beta_z_market: number;
    beta_z_sector: number;
    ljung_box_p: number;
    jarque_bera_p: number;
    skewness: number;
    excess_kurtosis: number;
    arch_lm_p: number;
  };
  variance: {
    market: Interval;
    sector: Interval;
    own: Interval;
    level: number;
    n_boot: number;
    block: number;
  };
}

export interface Daily {
  ticker: string;
  window: number;
  alpha: number;
  dates: string[];
  total: number[];
  market: number[];
  sector: number[];
  drift: number[];
  unexplained: number[];
  z: (number | null)[];
  p_value: (number | null)[];
  flagged: boolean[];
}

export interface Day {
  ticker: string;
  date: string;
  total: number;
  market: number;
  sector: number;
  drift: number;
  unexplained: number;
  se_market: number;
  se_sector: number;
  se_explained: number;
  beta_market: number;
  beta_sector: number;
  sigma: number | null;
  z: number | null;
  p_value: number | null;
  p_normal: number | null;
  return_period: number | null;
  flagged: boolean;
  alpha: number;
  window: number;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "network", "The server could not be reached. It may be waking up; try again in a minute.");
  }
  if (!response.ok) {
    let code = "http_error";
    let message = `Request failed (HTTP ${response.status})`;
    try {
      const body = (await response.json()) as { error?: { code: string; message: string } };
      if (body.error) ({ code, message } = body.error);
    } catch {
      // not JSON; keep the generic message
    }
    throw new ApiError(response.status, code, message);
  }
  return (await response.json()) as T;
}

const enc = encodeURIComponent;

export const api = {
  meta: (signal?: AbortSignal) => get<Meta>("/api/v1/meta", signal),
  search: (q: string, signal?: AbortSignal) =>
    get<{ results: StockRef[] }>(`/api/v1/stocks?q=${enc(q)}&limit=12`, signal).then((r) => r.results),
  profile: (ticker: string, signal?: AbortSignal) => get<Profile>(`/api/v1/stocks/${enc(ticker)}`, signal),
  daily: (ticker: string, signal?: AbortSignal) => get<Daily>(`/api/v1/stocks/${enc(ticker)}/daily`, signal),
  day: (ticker: string, date: string, signal?: AbortSignal) =>
    get<Day>(`/api/v1/stocks/${enc(ticker)}/days/${date}`, signal),
};
