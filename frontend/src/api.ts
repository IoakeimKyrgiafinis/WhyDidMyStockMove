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

// ---- M4: timeline, insiders, event studies ----

export interface Headline {
  headline: string;
  source: string;
  url: string;
  created_at: string;
}

export interface PressRelease {
  title: string;
  summary: string;
  url: string;
}

export type FilingCategory = "current" | "ownership" | "offering" | "tender" | "merger" | "report";

/** A company filing: an 8-K or 6-K, or a stake, offering, tender, merger or periodic report. */
export interface Filing {
  accession: string;
  form: string;
  category: FilingCategory;
  kind_label: string;
  minor: boolean;
  filing_date: string;
  accepted_at: string | null;
  items: string[];
  labels: string[];
  earnings: boolean;
  url: string;
  day: string;
  press: PressRelease | null;
}

export type Signal = "buy" | "sale" | "planned_sale" | "routine" | "other";

export interface InsiderFiling {
  accession: string;
  filing_date: string;
  trade_date: string | null;
  day: string | null;
  signal: Signal;
  signal_label: string;
  owner_name: string;
  owner_role: string;
  plan: string;
  shares: number | null;
  value: number | null;
  price: number | null;
  n_transactions: number;
  lag_days: number | null;
  url: string;
}

export interface MacroRelease {
  key: string;
  name: string;
  about: string;
  time_et: string;
  period: string;
  value: number;
  previous: number | null;
  unit: string;
  decimals: number;
  signed: boolean;
  url: string;
}

export interface DayDetail extends Day {
  items: { headlines: Headline[]; filings: Filing[]; insiders: InsiderFiling[]; macro: MacroRelease[] };
}

export interface Marker {
  date: string;
  headlines: number;
  filings: number;
  earnings: boolean;
  buys: number;
  sales: number;
}

export interface CarTest {
  start: number;
  end: number;
  mean_car: number;
  se: number | null;
  t_stat: number | null;
  p_value: number | null;
  bmp_p?: number | null;
  positive_share: number;
  ci_low: number | null;
  ci_high: number | null;
}

export interface StockStudy {
  n_requested: number;
  n_used: number;
  dropped_incomplete: number;
  dropped_overlap: number;
  relative_days: number[];
  mean_car: number[];
  ci_low: (number | null)[];
  ci_high: (number | null)[];
  tests: CarTest[];
  permutation: { observed: number; null_mean: number; null_std: number; p_value: number; n_permutations: number } | null;
}

export interface PooledStudy {
  relative_days: number[];
  mean_car: number[];
  ci_low: number[];
  ci_high: number[];
  tests: CarTest[];
  n_events: number;
  n_stocks: number;
  n_clusters: number;
  level: number;
}

export interface Insiders {
  ticker: string;
  filings: InsiderFiling[];
  counts: Partial<Record<Signal, number>>;
  study: StockStudy | null;
  earnings: {
    n_events: number;
    mean_abs_z_events: number;
    mean_abs_z_all: number;
    ratio: number;
    p_value: number;
    n_permutations: number;
  } | null;
  pooled: { as_of: string; pre: number; post: number; studies: Partial<Record<Signal, PooledStudy>> } | null;
}

// ---- M5: intraday reactions ----

export interface ReactionWindow {
  abnormal: number;
  z: number | null;
  minutes: number;
}

export interface HeadlineReaction {
  kind: "intraday" | "overnight";
  session: string;
  before: ReactionWindow | null;
  after: ReactionWindow;
  p_empirical: number | null;
  n_reference: number;
  flagged: boolean;
  moved_before: boolean;
  path: { minutes: number[]; actual: number[]; expected: number[]; band: number[] } | null;
}

export interface HeadlineGroup {
  headlines: Headline[];
  published: string; // New York time, no zone
  reaction: HeadlineReaction | null;
  error: string | null;
}

export interface Reactions {
  available: boolean;
  message: string | null;
  alpha: number;
  expected_false_flags: number;
  groups: HeadlineGroup[];
}

// ---- M6: peers ----

export type PeerLayer = "residual" | "raw" | "spillover";

export interface PeerRow {
  ticker: string;
  name: string;
  sector: string;
  corr_selection: number;
  ci_low_selection: number;
  ci_high_selection: number;
  corr_validation: number | null;
  ci_low_validation: number | null;
  ci_high_validation: number | null;
  spillover_beta: number | null;
  spillover_se: number | null;
}

export interface PeerSummary {
  target: string;
  selection_start: string;
  split: string;
  end: string;
  n_universe: number;
  peers: PeerRow[];
  order: string[];
  grids: Record<PeerLayer, (number | null)[][]>;
  fdr_q: number;
}

export interface CointegrationResult {
  ticker: string;
  statistic?: number;
  p_value?: number;
  hedge_ratio?: number;
  q_value?: number;
  cointegrated?: boolean;
  error?: string;
}

export interface Comove {
  target: string;
  date: string;
  own_unexplained: number;
  peers: { ticker: string; actual: number; expected: number; z: number | null; beta: number }[];
}

export interface ReplayFrame {
  date: string;
  residual: (number | null)[][];
  raw: (number | null)[][];
  mean_residual: number | null;
  mean_raw: number | null;
}

/** Open a Server-Sent Events stream. Returns a function that closes it. */
export function stream(
  path: string,
  handlers: Record<string, (data: unknown) => void>,
  onError: (error: ApiError) => void,
): () => void {
  const source = new EventSource(`${API_URL}${path}`);
  let finished = false;
  for (const [name, handle] of Object.entries(handlers)) {
    source.addEventListener(name, (event) => handle(JSON.parse((event as MessageEvent).data)));
  }
  source.addEventListener("done", () => {
    finished = true;
    source.close();
  });
  source.onerror = () => {
    if (finished) return;
    source.close();
    // EventSource hides the status; ask once more with fetch to get the API's message.
    const interrupted = new ApiError(0, "stream", "The connection was interrupted. Try again.");
    get<unknown>(path).then(
      () => onError(interrupted),
      (err: unknown) => onError(err instanceof ApiError ? err : interrupted),
    );
  };
  return () => {
    finished = true;
    source.close();
  };
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
      if (body.error) {
        code = body.error.code;
        // API messages are lower-case fragments; show them as sentences.
        message = body.error.message.charAt(0).toUpperCase() + body.error.message.slice(1);
      }
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
    get<DayDetail>(`/api/v1/stocks/${enc(ticker)}/days/${date}`, signal),
  markers: (ticker: string, signal?: AbortSignal) =>
    get<{ ticker: string; markers: Marker[] }>(`/api/v1/stocks/${enc(ticker)}/markers`, signal).then((r) => r.markers),
  insiders: (ticker: string, signal?: AbortSignal) => get<Insiders>(`/api/v1/stocks/${enc(ticker)}/insiders`, signal),
  comove: (ticker: string, date: string, signal?: AbortSignal) =>
    get<Comove>(`/api/v1/stocks/${enc(ticker)}/peers/comove?day=${date}`, signal),
  reactions: (ticker: string, date: string, signal?: AbortSignal) =>
    get<Reactions>(`/api/v1/stocks/${enc(ticker)}/days/${date}/reactions`, signal),
};
