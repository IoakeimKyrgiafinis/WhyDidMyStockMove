import { useEffect, useMemo, useRef, useState } from "react";
import type { Daily, Marker } from "../api";
import { fmtDate, fmtMonth, pct } from "../lib";
import type { Range } from "../range";

const PAD = { left: 48, right: 12, top: 10, bottom: 24 };

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(720);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function niceTicks(lo: number, hi: number, count = 5): number[] {
  const span = hi - lo || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) ?? raw;
  const ticks: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-12; v += step) ticks.push(Math.abs(v) < 1e-12 ? 0 : v);
  return ticks;
}

function dateTicks(lo: number, hi: number, maxTicks: number): number[] {
  const n = hi - lo + 1;
  const every = Math.max(1, Math.ceil(n / maxTicks));
  const out: number[] = [];
  for (let i = lo; i <= hi; i += every) out.push(i);
  return out;
}

interface ChartProps {
  daily: Daily;
  range: Range;
  selected: number | null;
  onSelect: (index: number) => void;
  markers?: Map<string, Marker>;
}

/** Cumulative return of the stock, and how much of it each source contributed. */
export function CumulativeChart({ daily, range }: Omit<ChartProps, "selected" | "onSelect">) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const height = 240;
  const [lo, hi] = range;

  const series = useMemo(() => {
    const stock: number[] = [];
    const market: number[] = [];
    const sector: number[] = [];
    const own: number[] = [];
    let growth = 1;
    let m = 0;
    let s = 0;
    let o = 0;
    for (let i = lo; i <= hi; i++) {
      growth *= 1 + daily.total[i];
      m += daily.market[i];
      s += daily.sector[i];
      o += daily.drift[i] + daily.unexplained[i];
      stock.push(growth - 1);
      market.push(m);
      sector.push(s);
      own.push(o);
    }
    return { stock, market, sector, own };
  }, [daily, lo, hi]);

  const all = [...series.stock, ...series.market, ...series.sector, ...series.own, 0];
  const yMin = Math.min(...all);
  const yMax = Math.max(...all);
  const pad = (yMax - yMin) * 0.08 || 0.01;
  const y0 = yMin - pad;
  const y1 = yMax + pad;
  const innerW = width - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom;
  const n = hi - lo + 1;
  const x = (k: number) => PAD.left + (n === 1 ? innerW / 2 : (k / (n - 1)) * innerW);
  const y = (v: number) => PAD.top + ((y1 - v) / (y1 - y0)) * innerH;
  const path = (values: number[]) => values.map((v, k) => `${k ? "L" : "M"}${x(k).toFixed(1)},${y(v).toFixed(1)}`).join("");

  const [hover, setHover] = useState<number | null>(null);
  const onMove = (event: React.PointerEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const k = Math.round(((event.clientX - box.left) / box.width) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, k)));
  };

  return (
    <div className="chart" ref={ref}>
      <svg width={width} height={height} role="img" aria-label="Cumulative return and its sources">
        {niceTicks(y0, y1).map((t) => (
          <g key={t}>
            <line className={t === 0 ? "axis-zero" : "grid"} x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} />
            <text className="tick" x={PAD.left - 6} y={y(t)} dy="0.32em" textAnchor="end">
              {pct(t, Math.abs(y1 - y0) < 0.05 ? 1 : 0, false)}
            </text>
          </g>
        ))}
        {dateTicks(lo, hi, Math.floor(innerW / 90)).map((i) => (
          <text key={i} className="tick" x={x(i - lo)} y={height - 6} textAnchor="middle">
            {fmtMonth(daily.dates[i])}
          </text>
        ))}
        <path className="line line--market" d={path(series.market)} />
        <path className="line line--sector" d={path(series.sector)} />
        <path className="line line--own" d={path(series.own)} />
        <path className="line line--stock" d={path(series.stock)} />
        {hover !== null && (
          <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + innerH} />
        )}
        <rect
          x={PAD.left}
          y={PAD.top}
          width={innerW}
          height={innerH}
          fill="transparent"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: Math.min(x(hover) + 12, width - 190) }}>
          <strong>{fmtDate(daily.dates[lo + hover])}</strong>
          <Row cls="stock" label="Stock (compounded)" value={pct(series.stock[hover])} />
          <Row cls="market" label="Market" value={pct(series.market[hover])} />
          <Row cls="sector" label="Sector" value={pct(series.sector[hover])} />
          <Row cls="own" label="Own" value={pct(series.own[hover])} />
        </div>
      )}
    </div>
  );
}

function Row({ cls, label, value }: { cls: string; label: string; value: string }) {
  return (
    <div className="tooltip__row">
      <span className={`swatch swatch--${cls}`} />
      <span>{label}</span>
      <span className="num">{value}</span>
    </div>
  );
}

/** Each day's return as stacked market, sector and own parts. Click a day to explain it. */
export function DailyChart({ daily, range, selected, onSelect, markers }: ChartProps) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const height = 244;
  const [lo, hi] = range;
  const n = hi - lo + 1;
  const innerW = width - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom - 30;
  const lane = PAD.top + innerH + 22; // timeline markers sit in a lane under the flags

  const own = (i: number) => daily.drift[i] + daily.unexplained[i];
  let yMin = 0;
  let yMax = 0;
  for (let i = lo; i <= hi; i++) {
    const parts = [daily.market[i], daily.sector[i], own(i)];
    yMax = Math.max(yMax, parts.filter((v) => v > 0).reduce((a, b) => a + b, 0));
    yMin = Math.min(yMin, parts.filter((v) => v < 0).reduce((a, b) => a + b, 0));
  }
  const pad = (yMax - yMin) * 0.06 || 0.005;
  const y0 = yMin - pad;
  const y1 = yMax + pad;
  const band = innerW / n;
  const barW = Math.max(0.6, band * 0.78);
  const x = (k: number) => PAD.left + k * band + band / 2;
  const y = (v: number) => PAD.top + ((y1 - v) / (y1 - y0)) * innerH;

  const [hover, setHover] = useState<number | null>(null);
  const indexAt = (event: React.MouseEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    return Math.max(0, Math.min(n - 1, Math.floor(((event.clientX - box.left) / box.width) * n)));
  };

  const bars: React.ReactNode[] = [];
  for (let k = 0; k < n; k++) {
    const i = lo + k;
    let up = 0;
    let down = 0;
    for (const [cls, v] of [
      ["market", daily.market[i]],
      ["sector", daily.sector[i]],
      ["own", own(i)],
    ] as const) {
      const start = v >= 0 ? up : down;
      const end = start + v;
      if (v >= 0) up = end;
      else down = end;
      const top = y(Math.max(start, end));
      const h = Math.abs(y(start) - y(end));
      if (h < 0.2) continue;
      bars.push(<rect key={`${i}${cls}`} className={`bar bar--${cls}`} x={x(k) - barW / 2} y={top} width={barW} height={h} />);
    }
    if (daily.flagged[i]) {
      bars.push(<circle key={`${i}f`} className="flag" cx={x(k)} cy={PAD.top + innerH + 6} r={Math.min(3, Math.max(1.6, barW / 2))} />);
    }
    const m = markers?.get(daily.dates[i]);
    if (m) bars.push(<MarkerGlyph key={`${i}m`} marker={m} cx={x(k)} cy={lane} />);
  }

  const tip = hover !== null ? lo + hover : null;

  return (
    <div className="chart" ref={ref}>
      <svg width={width} height={height} role="img" aria-label="Daily return split into market, sector and own parts">
        {niceTicks(y0, y1, 4).map((t) => (
          <g key={t}>
            <line className={t === 0 ? "axis-zero" : "grid"} x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} />
            <text className="tick" x={PAD.left - 6} y={y(t)} dy="0.32em" textAnchor="end">
              {pct(t, 1, false)}
            </text>
          </g>
        ))}
        {selected !== null && selected >= lo && selected <= hi && (
          <rect className="selected-band" x={x(selected - lo) - Math.max(band, 3) / 2} y={PAD.top} width={Math.max(band, 3)} height={innerH + 30} />
        )}
        {bars}
        {dateTicks(lo, hi, Math.floor(innerW / 90)).map((i) => (
          <text key={i} className="tick" x={x(i - lo)} y={height - 4} textAnchor="middle">
            {fmtMonth(daily.dates[i])}
          </text>
        ))}
        <rect
          x={PAD.left}
          y={PAD.top}
          width={innerW}
          height={innerH + 30}
          fill="transparent"
          style={{ cursor: "pointer" }}
          onPointerMove={(e) => setHover(indexAt(e))}
          onPointerLeave={() => setHover(null)}
          onClick={(e) => onSelect(lo + indexAt(e))}
        />
      </svg>
      {tip !== null && hover !== null && (
        <div className="tooltip" style={{ left: Math.min(x(hover) + 12, width - 190) }}>
          <strong>{fmtDate(daily.dates[tip])}</strong>
          <Row cls="stock" label="Total" value={pct(daily.total[tip])} />
          <Row cls="market" label="Market" value={pct(daily.market[tip])} />
          <Row cls="sector" label="Sector" value={pct(daily.sector[tip])} />
          <Row cls="own" label="Own" value={pct(own(tip))} />
          {daily.flagged[tip] && <div className="tooltip__flag">Unusual unexplained move</div>}
          {markers?.get(daily.dates[tip]) && <MarkerSummary marker={markers.get(daily.dates[tip])!} />}
          <div className="tooltip__hint">Click to explain this day</div>
        </div>
      )}
    </div>
  );
}

/** The most important thing that happened on a day: earnings, then insider trades, then
 * other filings, then headlines. */
function MarkerGlyph({ marker: m, cx, cy }: { marker: Marker; cx: number; cy: number }) {
  if (m.earnings) return <rect className="mk mk--earnings" x={cx - 3.5} y={cy - 3.5} width={7} height={7} />;
  if (m.buys) return <path className="mk mk--buy" d={`M${cx},${cy - 4}L${cx + 4},${cy + 3}L${cx - 4},${cy + 3}Z`} />;
  if (m.sales) return <path className="mk mk--sale" d={`M${cx},${cy + 4}L${cx + 4},${cy - 3}L${cx - 4},${cy - 3}Z`} />;
  if (m.filings) return <path className="mk mk--filing" d={`M${cx},${cy - 4}L${cx + 4},${cy}L${cx},${cy + 4}L${cx - 4},${cy}Z`} />;
  return <circle className="mk mk--news" cx={cx} cy={cy} r={Math.min(3.5, 1.5 + Math.log2(1 + m.headlines) / 2)} />;
}

function MarkerSummary({ marker: m }: { marker: Marker }) {
  const parts: string[] = [];
  if (m.earnings) parts.push("earnings filing");
  else if (m.filings) parts.push(`${m.filings} 8-K filing${m.filings > 1 ? "s" : ""}`);
  if (m.buys) parts.push(`${m.buys} insider purchase${m.buys > 1 ? "s" : ""}`);
  if (m.sales) parts.push(`${m.sales} insider sale${m.sales > 1 ? "s" : ""}`);
  if (m.headlines) parts.push(`${m.headlines} headline${m.headlines > 1 ? "s" : ""}`);
  return <div className="tooltip__events">{parts.join(" · ")}</div>;
}

export function MarkerLegend() {
  return (
    <div className="legend legend--markers">
      <span>
        <svg width="10" height="10"><circle className="flag" cx="5" cy="5" r="3" /></svg>
        Unusual
      </span>
      <span>
        <svg width="10" height="10"><rect className="mk mk--earnings" x="1.5" y="1.5" width="7" height="7" /></svg>
        Earnings
      </span>
      <span>
        <svg width="10" height="10"><path className="mk mk--buy" d="M5,1L9,8L1,8Z" /></svg>
        Insider buy
      </span>
      <span>
        <svg width="10" height="10"><path className="mk mk--sale" d="M5,9L9,2L1,2Z" /></svg>
        Insider sale
      </span>
      <span>
        <svg width="10" height="10"><path className="mk mk--filing" d="M5,1L9,5L5,9L1,5Z" /></svg>
        8-K
      </span>
      <span>
        <svg width="10" height="10"><circle className="mk mk--news" cx="5" cy="5" r="2.5" /></svg>
        News
      </span>
    </div>
  );
}

/** Average cumulative abnormal return around events, with its confidence band. */
export function EventPathChart({
  days,
  mean,
  low,
  high,
  label,
}: {
  days: number[];
  mean: number[];
  low: (number | null)[];
  high: (number | null)[];
  label: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const height = 200;
  const values = [...mean, ...low.filter((v): v is number => v !== null), ...high.filter((v): v is number => v !== null), 0];
  const pad = (Math.max(...values) - Math.min(...values)) * 0.1 || 0.005;
  const y0 = Math.min(...values) - pad;
  const y1 = Math.max(...values) + pad;
  const innerW = width - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom;
  const n = days.length;
  const x = (k: number) => PAD.left + (k / Math.max(n - 1, 1)) * innerW;
  const y = (v: number) => PAD.top + ((y1 - v) / (y1 - y0)) * innerH;
  const zero = days.indexOf(0);

  const band: string[] = [];
  for (let k = 0; k < n; k++) if (high[k] !== null) band.push(`${band.length ? "L" : "M"}${x(k)},${y(high[k]!)}`);
  for (let k = n - 1; k >= 0; k--) if (low[k] !== null) band.push(`L${x(k)},${y(low[k]!)}`);

  return (
    <div className="chart" ref={ref}>
      <svg width={width} height={height} role="img" aria-label={label}>
        {niceTicks(y0, y1, 4).map((t) => (
          <g key={t}>
            <line className={t === 0 ? "axis-zero" : "grid"} x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} />
            <text className="tick" x={PAD.left - 6} y={y(t)} dy="0.32em" textAnchor="end">
              {pct(t, 1, false)}
            </text>
          </g>
        ))}
        {zero >= 0 && <line className="crosshair" x1={x(zero)} x2={x(zero)} y1={PAD.top} y2={PAD.top + innerH} />}
        {band.length > 2 && <path className="ci-band" d={`${band.join("")}Z`} />}
        <path className="line line--own" d={mean.map((v, k) => `${k ? "L" : "M"}${x(k)},${y(v)}`).join("")} />
        {days.map((d, k) =>
          d % 5 === 0 ? (
            <text key={d} className="tick" x={x(k)} y={height - 6} textAnchor="middle">
              {d > 0 ? `+${d}` : d}
            </text>
          ) : null,
        )}
      </svg>
    </div>
  );
}

/** Cumulative stock return around a headline next to what market and sector predicted. */
export function ReactionChart({
  minutes,
  actual,
  expected,
  band,
}: {
  minutes: number[];
  actual: number[];
  expected: number[];
  band: number[];
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const height = 170;
  const lows = expected.map((e, k) => e - band[k]);
  const highs = expected.map((e, k) => e + band[k]);
  const all = [...actual, ...lows, ...highs, 0];
  const pad = (Math.max(...all) - Math.min(...all)) * 0.1 || 0.002;
  const y0 = Math.min(...all) - pad;
  const y1 = Math.max(...all) + pad;
  const innerW = width - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom;
  const lo = minutes[0];
  const hi = minutes[minutes.length - 1];
  const x = (m: number) => PAD.left + ((m - lo) / Math.max(hi - lo, 1)) * innerW;
  const y = (v: number) => PAD.top + ((y1 - v) / (y1 - y0)) * innerH;
  const line = (values: number[]) => values.map((v, k) => `${k ? "L" : "M"}${x(minutes[k])},${y(v)}`).join("");
  const bandPath = `${highs.map((v, k) => `${k ? "L" : "M"}${x(minutes[k])},${y(v)}`).join("")}${lows
    .map((v, k) => [k, v] as const)
    .reverse()
    .map(([k, v]) => `L${x(minutes[k])},${y(v)}`)
    .join("")}Z`;

  return (
    <div className="chart" ref={ref}>
      <svg width={width} height={height} role="img" aria-label="Stock path around the headline against the expected path">
        {niceTicks(y0, y1, 4).map((t) => (
          <g key={t}>
            <line className={t === 0 ? "axis-zero" : "grid"} x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} />
            <text className="tick" x={PAD.left - 6} y={y(t)} dy="0.32em" textAnchor="end">
              {pct(t, 1, false)}
            </text>
          </g>
        ))}
        {lo <= 0 && hi >= 0 && <line className="crosshair" x1={x(0)} x2={x(0)} y1={PAD.top} y2={PAD.top + innerH} />}
        <path className="ci-band ci-band--expected" d={bandPath} />
        <path className="line line--expected" d={line(expected)} />
        <path className="line line--stock" d={line(actual)} />
        {minutes
          .filter((m) => m % 15 === 0)
          .map((m) => (
            <text key={m} className="tick" x={x(m)} y={height - 6} textAnchor="middle">
              {m === 0 ? "headline" : `${m > 0 ? "+" : ""}${m}m`}
            </text>
          ))}
      </svg>
      <div className="legend">
        <span>
          <span className="swatch swatch--stock" />
          Stock
        </span>
        <span>
          <span className="swatch swatch--market" />
          Expected from market and sector (95% band)
        </span>
      </div>
    </div>
  );
}
