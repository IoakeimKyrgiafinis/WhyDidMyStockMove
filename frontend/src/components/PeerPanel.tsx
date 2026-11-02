import { useEffect, useMemo, useState } from "react";
import { api, stream, type CointegrationResult, type PeerLayer, type PeerSummary, type ReplayFrame } from "../api";
import { fmtDate, num, pct, pText, titleCase, useAsync } from "../lib";
import { Info } from "./Info";
import { ColorScale, GridCanvas } from "./PeerGrid";

const LAYERS: { key: PeerLayer; label: string; help: string }[] = [
  {
    key: "residual",
    label: "Beyond market and sector",
    help: "Correlation of the parts of each day's move that the market and the stock's sector do not explain.",
  },
  { key: "raw", label: "Raw", help: "Plain correlation of daily returns. Mostly reflects that all stocks share the market." },
  {
    key: "spillover",
    label: "Spillover",
    help: "Row stock's unexplained move per 1% unexplained move of the column stock (a regression slope).",
  },
];

function scaleFor(layer: PeerLayer, matrix: (number | null)[][]): number {
  if (layer === "raw") return 1;
  let max = 0;
  matrix.forEach((row, i) =>
    row.forEach((v, j) => {
      if (i !== j && v !== null) max = Math.max(max, Math.abs(v));
    }),
  );
  return Math.max(0.1, Math.min(1, Math.ceil(max * 10) / 10));
}

export function PeerPanel({ ticker, day, onTicker }: { ticker: string; day: string | null; onTicker: (t: string) => void }) {
  const [summary, setSummary] = useState<PeerSummary | null>(null);
  const [coint, setCoint] = useState<Record<string, CointegrationResult>>({});
  const [error, setError] = useState<string | null>(null);
  const [layer, setLayer] = useState<PeerLayer>("residual");
  const [hover, setHover] = useState<{ row: number; col: number } | null>(null);

  // The stock page remounts per ticker, so state starts fresh for each stream.
  useEffect(() => {
    return stream(
      `/api/v1/stocks/${encodeURIComponent(ticker)}/peers/stream`,
      {
        peers: (data) => setSummary(data as PeerSummary),
        cointegration: (data) => {
          const row = data as CointegrationResult;
          setCoint((c) => ({ ...c, [row.ticker]: { ...c[row.ticker], ...row } }));
        },
        fdr: (data) =>
          setCoint((c) => {
            const next = { ...c };
            for (const row of data as CointegrationResult[]) next[row.ticker] = { ...next[row.ticker], ...row };
            return next;
          }),
      },
      (err) => setError(err.message),
    );
  }, [ticker]);

  const matrix = summary?.grids[layer];
  const scale = useMemo(() => (matrix ? scaleFor(layer, matrix) : 1), [layer, matrix]);

  return (
    <section className="card">
      <header className="card__head">
        <h2>Stocks that move with {ticker}</h2>
        <Info label="peer grid">
          <p>
            Every stock in the universe is ranked by how closely its daily moves track {ticker}'s once the market and
            each stock's sector are removed. Raw correlation would mostly list stocks that simply follow the market.
          </p>
          <p>
            Peers are picked on an earlier two-year window and then checked on the last year, which played no part in
            the choice. Picking the best of thousands flatters the numbers (the winner's curse), so expect the later
            correlations to be lower. A real link holds up; a lucky one fades.
          </p>
          <p>
            Cointegration asks a different question: whether two price levels stay tied together over the long run. It
            is tested on the last year for the top 10, with a Benjamini-Hochberg correction for testing several at once.
          </p>
        </Info>
      </header>

      {error && <p className="error">{error}</p>}
      {!summary && !error && <p className="muted">Ranking {ticker} against every stock in the universe...</p>}
      {summary && matrix && (
        <>
          <p className="muted small">
            {summary.peers.length} closest of {summary.n_universe.toLocaleString()} stocks with complete prices. Chosen
            on {fmtDate(summary.selection_start)} to {fmtDate(summary.split)}; grid and checks on {fmtDate(summary.split)}{" "}
            to {fmtDate(summary.end)}. Computed from daily closes, updated each evening.
          </p>
          <div className="filters" role="group" aria-label="Grid layer">
            {LAYERS.map((l) => (
              <button
                key={l.key}
                type="button"
                title={l.help}
                className={`chip${layer === l.key ? " is-active" : ""}`}
                onClick={() => setLayer(l.key)}
              >
                {l.label}
              </button>
            ))}
          </div>
          <div className="peer-grid">
            <div className="peer-grid__canvas">
              <GridCanvas names={summary.order} matrix={matrix} scale={scale} highlight={ticker} onHover={setHover} />
              <ColorScale scale={scale} label={LAYERS.find((l) => l.key === layer)!.label} />
            </div>
            <div className="peer-grid__hover">
              {hover ? (
                <HoverCard summary={summary} row={hover.row} col={hover.col} />
              ) : (
                <p className="muted small">
                  Hover the grid to compare any two stocks. {ticker} is outlined; related stocks are clustered together.
                </p>
              )}
            </div>
          </div>
          <PeerTable summary={summary} coint={coint} onTicker={onTicker} />
          {day && <Comove ticker={ticker} day={day} />}
          <Replay ticker={ticker} />
        </>
      )}
    </section>
  );
}

function HoverCard({ summary, row, col }: { summary: PeerSummary; row: number; col: number }) {
  const a = summary.order[row];
  const b = summary.order[col];
  if (a === b) return <p className="small">{a}</p>;
  const v = (layer: PeerLayer) => summary.grids[layer][row][col];
  const fmt = (x: number | null) => (x === null ? "n/a" : num(x, 2));
  return (
    <dl className="facts facts--hover">
      <div className="facts__title">
        {a} and {b}
      </div>
      <div>
        <dt>Beyond market and sector</dt>
        <dd className="num">{fmt(v("residual"))}</dd>
      </div>
      <div>
        <dt>Raw correlation</dt>
        <dd className="num">{fmt(v("raw"))}</dd>
      </div>
      <div>
        <dt>
          {a} per 1% of {b}
        </dt>
        <dd className="num">{fmt(v("spillover"))}</dd>
      </div>
    </dl>
  );
}

function PeerTable({
  summary,
  coint,
  onTicker,
}: {
  summary: PeerSummary;
  coint: Record<string, CointegrationResult>;
  onTicker: (t: string) => void;
}) {
  return (
    <div className="table-wrap">
      <table className="trades peers-table">
        <thead>
          <tr>
            <th>Peer</th>
            <th className="num" title="Correlation on the window the peers were chosen on">
              Chosen on
            </th>
            <th className="num" title="Correlation on the later year, with a 95% interval">
              Last year
            </th>
            <th className="num" title="Peer's unexplained move per 1% of this stock's">
              Spillover
            </th>
            <th title="Engle-Granger on log prices over the last year">Cointegration</th>
          </tr>
        </thead>
        <tbody>
          {summary.peers.slice(0, 10).map((p) => {
            const c = coint[p.ticker];
            const held = p.corr_validation !== null && p.ci_low_validation !== null && p.ci_low_validation > 0;
            return (
              <tr key={p.ticker}>
                <td>
                  <button type="button" className="link" onClick={() => onTicker(p.ticker)}>
                    {p.ticker}
                  </button>
                  <span className="muted small"> {titleCase(p.name)}</span>
                </td>
                <td className="num">{num(p.corr_selection)}</td>
                <td className={`num${held ? "" : " muted"}`}>
                  {p.corr_validation !== null ? num(p.corr_validation) : "–"}
                  {p.ci_low_validation !== null && p.ci_high_validation !== null && (
                    <span className="muted small">
                      {" "}
                      ({num(p.ci_low_validation)} to {num(p.ci_high_validation)})
                    </span>
                  )}
                </td>
                <td className="num">{p.spillover_beta !== null ? num(p.spillover_beta) : "–"}</td>
                <td>
                  {!c ? (
                    <span className="muted small">testing...</span>
                  ) : c.error ? (
                    <span className="muted small">n/a</span>
                  ) : c.cointegrated === undefined ? (
                    <span className="small">{c.p_value !== undefined ? pText(c.p_value) : ""}</span>
                  ) : c.cointegrated ? (
                    <span className="tag tag--buy" title={`q = ${c.q_value?.toFixed(3)}`}>
                      Tied (q {c.q_value?.toFixed(3)})
                    </span>
                  ) : (
                    <span className="muted small">not tied (q {c.q_value?.toFixed(2)})</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted small">
        Faded "last year" values have an interval that includes zero: the link did not clearly hold up.
      </p>
    </div>
  );
}

function Comove({ ticker, day }: { ticker: string; day: string }) {
  const result = useAsync(`${ticker}/comove/${day}`, (signal) => api.comove(ticker, day, signal));
  if (result.state === "loading") return <p className="muted small">Checking the peers on {fmtDate(day)}...</p>;
  if (result.state === "error") return <p className="muted small">Peers on {fmtDate(day)}: {result.error.message}</p>;
  const c = result.data;
  const scale = Math.max(0.005, ...c.peers.flatMap((p) => [Math.abs(p.actual), Math.abs(p.expected)]));
  const w = (v: number) => `${(Math.abs(v) / scale) * 50}%`;
  const left = (v: number) => (v >= 0 ? "50%" : `${50 - (Math.abs(v) / scale) * 50}%`);
  return (
    <div className="study">
      <header className="card__head">
        <h3>Did the peers follow on {fmtDate(day)}?</h3>
        <Info label="co-move">
          <p>
            {ticker}'s unexplained move that day was {pct(c.own_unexplained)}. For each peer, the thin bar is what its
            historical spillover predicts from that; the thick bar is the peer's own unexplained move. The score says
            how far apart they were, in the peer's usual daily noise. Spillover is a historical average and does not
            show that one stock's move caused another's.
          </p>
        </Info>
      </header>
      <div className="comove">
        {c.peers.map((p) => (
          <div key={p.ticker} className="comove__row">
            <span className="num">{p.ticker}</span>
            <span className="comove__bar">
              <span className="contrib__axis" />
              <span className="comove__actual" style={{ width: w(p.actual), left: left(p.actual) }} />
              <span className="comove__expected" style={{ width: w(p.expected), left: left(p.expected) }} />
            </span>
            <span className="num small">
              {pct(p.actual, 1)} vs {pct(p.expected, 1)}
              {p.z !== null && <span className="muted"> · {num(p.z, 1)}σ</span>}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Replay({ ticker }: { ticker: string }) {
  const [names, setNames] = useState<string[] | null>(null);
  const [frames, setFrames] = useState<ReplayFrame[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [index, setIndex] = useState(0);
  const [follow, setFollow] = useState(true);
  const [layer, setLayer] = useState<"residual" | "raw">("raw");

  useEffect(() => {
    if (!running) return;
    return stream(
      `/api/v1/stocks/${encodeURIComponent(ticker)}/peers/replay`,
      {
        names: (data) => setNames((data as { names: string[] }).names),
        frame: (data) => setFrames((f) => [...f, data as ReplayFrame]),
      },
      (err) => {
        setError(err.message);
        setRunning(false);
      },
    );
  }, [running, ticker]);

  const shown = follow ? frames.length - 1 : Math.min(index, frames.length - 1);
  const frame = frames[shown];

  return (
    <div className="study">
      <header className="card__head">
        <h3>Replay the history</h3>
        <Info label="replay">
          <p>
            Correlations among {ticker} and its top 19 peers over a rolling three-month window, every week of the
            history. Each frame is computed as it is sent. Raw correlations jump together in a crash (watch March 2020);
            correlations beyond market and sector move much less.
          </p>
        </Info>
      </header>
      {!running && frames.length === 0 ? (
        <button type="button" className="button" onClick={() => setRunning(true)}>
          Replay correlations over time
        </button>
      ) : (
        <>
          {error && <p className="error">{error}</p>}
          <div className="filters">
            {(["raw", "residual"] as const).map((k) => (
              <button key={k} type="button" className={`chip${layer === k ? " is-active" : ""}`} onClick={() => setLayer(k)}>
                {k === "raw" ? "Raw" : "Beyond market and sector"}
              </button>
            ))}
          </div>
          {names && frame && (
            <div className="peer-grid">
              <div className="peer-grid__canvas peer-grid__canvas--small">
                <GridCanvas names={names} matrix={frame[layer]} scale={1} highlight={ticker} size={260} />
              </div>
              <div className="replay__side">
                <p className="lede num">{fmtDate(frame.date)}</p>
                <p className="small">
                  Average correlation: raw {frame.mean_raw !== null ? num(frame.mean_raw) : "n/a"}, beyond market and
                  sector {frame.mean_residual !== null ? num(frame.mean_residual) : "n/a"}
                </p>
                <MeanLines frames={frames} at={shown} />
                <input
                  type="range"
                  min={0}
                  max={Math.max(frames.length - 1, 0)}
                  value={shown}
                  aria-label="Replay position"
                  onChange={(e) => {
                    setFollow(false);
                    setIndex(Number(e.target.value));
                  }}
                />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function MeanLines({ frames, at }: { frames: ReplayFrame[]; at: number }) {
  const width = 300;
  const height = 90;
  if (frames.length < 2) return null;
  const x = (i: number) => (i / (frames.length - 1)) * width;
  const y = (v: number) => height - Math.max(0, Math.min(1, (v + 0.1) / 1.1)) * height;
  const line = (key: "mean_raw" | "mean_residual") =>
    frames
      .map((f, i) => (f[key] === null ? "" : `${i && frames[i - 1][key] !== null ? "L" : "M"}${x(i)},${y(f[key]!)}`))
      .join("");
  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} className="meanlines" role="img" aria-label="Average correlation over time">
      <path className="line line--stock" d={line("mean_raw")} />
      <path className="line line--own" d={line("mean_residual")} />
      <line className="crosshair" x1={x(at)} x2={x(at)} y1={0} y2={height} />
    </svg>
  );
}
