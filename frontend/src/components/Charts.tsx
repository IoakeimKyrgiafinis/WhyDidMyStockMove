import { useEffect, useMemo, useRef, useState } from "react";
import type { Daily } from "../api";
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
export function DailyChart({ daily, range, selected, onSelect }: ChartProps) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const height = 220;
  const [lo, hi] = range;
  const n = hi - lo + 1;
  const innerW = width - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom - 8;

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
          <rect className="selected-band" x={x(selected - lo) - Math.max(band, 3) / 2} y={PAD.top} width={Math.max(band, 3)} height={innerH + 10} />
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
          height={innerH + 10}
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
          <div className="tooltip__hint">Click to explain this day</div>
        </div>
      )}
    </div>
  );
}
