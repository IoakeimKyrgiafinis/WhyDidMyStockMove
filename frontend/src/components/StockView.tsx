import { useState } from "react";
import { api, type Daily, type Profile } from "../api";
import { fmtDate, titleCase, useAsync } from "../lib";
import { Attribution } from "./Attribution";
import { CumulativeChart, DailyChart } from "./Charts";
import { DayPanel } from "./DayPanel";
import { HealthCard } from "./HealthCard";
import { Info } from "./Info";
import { presetRange, type Range } from "../range";
import { RangePicker } from "./RangePicker";

interface Props {
  ticker: string;
  day: string | null;
  onDay: (day: string | null) => void;
}

export function StockView({ ticker, day, onDay }: Props) {
  const data = useAsync(ticker, (signal) =>
    Promise.all([api.profile(ticker, signal), api.daily(ticker, signal)]),
  );

  if (data.state === "loading") return <p className="status">Loading {ticker}...</p>;
  if (data.state === "error") {
    return (
      <div className="status status--error">
        <h2>{ticker}</h2>
        <p>{data.error.message}</p>
      </div>
    );
  }
  const [profile, daily] = data.data;
  return <Loaded key={ticker} profile={profile} daily={daily} day={day} onDay={onDay} />;
}

function Loaded({ profile, daily, day, onDay }: { profile: Profile; daily: Daily; day: string | null; onDay: Props["onDay"] }) {
  const [chosen, setRange] = useState<Range>(() => presetRange(daily.dates, "1Y"));
  const selected = day ? daily.dates.indexOf(day) : -1;
  // A shared link may point at a day outside the chosen range: widen the range to show it.
  const range: Range =
    selected >= 0 && (selected < chosen[0] || selected > chosen[1])
      ? [Math.min(selected, chosen[0]), Math.max(selected, chosen[1])]
      : chosen;

  const s = profile.stock;
  const lastMove = daily.total[daily.total.length - 1];

  return (
    <div className="stock">
      <header className="stock__head">
        <div>
          <h1>
            {s.ticker} <span className="stock__name">{titleCase(s.name)}</span>
          </h1>
          <p className="muted">
            {s.sector} · sector factor {s.sector_etf} · market factor IWV
          </p>
        </div>
        <div className="stock__price">
          <span className="num big">${profile.last_close.toFixed(2)}</span>
          <span className={`num ${lastMove >= 0 ? "pos" : "neg"}`}>
            {lastMove >= 0 ? "+" : ""}
            {(lastMove * 100).toFixed(2)}%
          </span>
          <span className="muted small">close {fmtDate(profile.last_date)}</span>
        </div>
      </header>

      <RangePicker dates={daily.dates} range={range} onChange={setRange} />

      <div className="grid">
        <div className="grid__main">
          <Attribution daily={daily} range={range} />
          <section className="card">
            <header className="card__head">
              <h2>Cumulative return and its sources</h2>
              <Legend />
            </header>
            <CumulativeChart daily={daily} range={range} />
          </section>
          <section className="card">
            <header className="card__head">
              <h2>Day by day</h2>
              <Info label="daily chart">
                <p>
                  Each bar is one trading day, split into what the market explains, what the sector explains, and the
                  stock's own part. Dots below the axis mark days whose own move was unusual for this stock. Click any
                  day to see it explained in detail.
                </p>
              </Info>
            </header>
            <DailyChart
              daily={daily}
              range={range}
              selected={selected >= 0 ? selected : null}
              onSelect={(i) => onDay(daily.dates[i])}
            />
            <LatestFlags daily={daily} range={range} onDay={onDay} />
          </section>
        </div>
        <aside className="grid__side">
          {day && selected >= 0 ? (
            <DayPanel ticker={s.ticker} date={day} onClose={() => onDay(null)} />
          ) : (
            <section className="card card--hint">
              <h2>Pick a day</h2>
              <p className="muted">
                Click a bar in the day-by-day chart, or one of the unusual days listed under it, to see how much of
                that day's move the market and sector explain.
              </p>
              <button type="button" className="button" onClick={() => onDay(daily.dates[daily.dates.length - 1])}>
                Explain the latest day
              </button>
            </section>
          )}
          <HealthCard profile={profile} />
        </aside>
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className="legend">
      <span>
        <span className="swatch swatch--stock" />
        Stock
      </span>
      <span>
        <span className="swatch swatch--market" />
        Market
      </span>
      <span>
        <span className="swatch swatch--sector" />
        Sector
      </span>
      <span>
        <span className="swatch swatch--own" />
        Own
      </span>
    </div>
  );
}

function LatestFlags({ daily, range, onDay }: { daily: Daily; range: Range; onDay: Props["onDay"] }) {
  const flags: number[] = [];
  for (let i = range[1]; i >= range[0] && flags.length < 6; i--) if (daily.flagged[i]) flags.push(i);
  if (flags.length === 0) return <p className="muted small">No unusual own moves in this period.</p>;
  return (
    <div className="flags">
      <span className="muted small">Recent unusual own moves:</span>
      {flags.map((i) => (
        <button key={i} type="button" className="chip" onClick={() => onDay(daily.dates[i])}>
          {fmtDate(daily.dates[i])}{" "}
          <span className={daily.unexplained[i] >= 0 ? "pos" : "neg"}>
            {daily.unexplained[i] >= 0 ? "+" : ""}
            {(daily.unexplained[i] * 100).toFixed(1)}%
          </span>
        </button>
      ))}
    </div>
  );
}
