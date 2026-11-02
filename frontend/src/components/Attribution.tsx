import { useMemo } from "react";
import type { Daily } from "../api";
import { fmtDate, pct, pts } from "../lib";
import { Info } from "./Info";
import type { Range } from "../range";

export function Attribution({ daily, range }: { daily: Daily; range: Range }) {
  const [lo, hi] = range;

  const a = useMemo(() => {
    let growth = 1;
    let market = 0;
    let sector = 0;
    let own = 0;
    let sum = 0;
    let flagged = 0;
    let scored = 0;
    for (let i = lo; i <= hi; i++) {
      growth *= 1 + daily.total[i];
      sum += daily.total[i];
      market += daily.market[i];
      sector += daily.sector[i];
      own += daily.drift[i] + daily.unexplained[i];
      if (daily.p_value[i] !== null) scored += 1;
      if (daily.flagged[i]) flagged += 1;
    }
    return { compounded: growth - 1, sum, market, sector, own, compounding: growth - 1 - sum, flagged, expected: daily.alpha * scored };
  }, [daily, lo, hi]);

  const parts = [
    { key: "market", label: "Market", value: a.market, help: "What the whole US market (IWV, the Russell 3000 ETF) did, scaled by this stock's sensitivity to it." },
    { key: "sector", label: "Sector", value: a.sector, help: "What the stock's sector did beyond the market, scaled by the stock's sensitivity to its sector." },
    { key: "own", label: "Own", value: a.own, help: "Everything left over: company news, but also noise and anything the model misses." },
  ];
  const scale = Math.max(...parts.map((p) => Math.abs(p.value)), 1e-9);
  const verb = a.compounded >= 0 ? "rose" : "fell";

  return (
    <section className="card">
      <header className="card__head">
        <h2>Where the move came from</h2>
        <Info label="period attribution">
          <p>
            Each day's return is split using sensitivities (betas) estimated on the previous year only. Adding the
            daily pieces gives each source's contribution to the period, in percentage points.
          </p>
          <p>
            Daily pieces add up to the sum of daily returns. The real price change compounds, so it differs
            slightly; that gap is shown as compounding and belongs to no single source.
          </p>
        </Info>
      </header>

      <p className="lede">
        {daily.ticker} {verb} <strong className={a.compounded >= 0 ? "pos" : "neg"}>{pct(Math.abs(a.compounded), 1, false)}</strong>{" "}
        between {fmtDate(daily.dates[lo])} and {fmtDate(daily.dates[hi])}.
      </p>

      <div className="contrib">
        {parts.map((p) => (
          <div className="contrib__row" key={p.key} title={p.help}>
            <span className="contrib__label">
              <span className={`swatch swatch--${p.key}`} />
              {p.label}
            </span>
            <span className="contrib__bar">
              <span className="contrib__axis" />
              <span
                className={`contrib__fill contrib__fill--${p.key}`}
                style={{
                  width: `${(Math.abs(p.value) / scale) * 50}%`,
                  left: p.value >= 0 ? "50%" : `${50 - (Math.abs(p.value) / scale) * 50}%`,
                }}
              />
            </span>
            <span className="num contrib__value">{pts(p.value)}</span>
          </div>
        ))}
        <div className="contrib__note">
          Compounding {pts(a.compounding)} · sum of days {pts(a.sum)} · price change {pct(a.compounded, 1)}
        </div>
      </div>

      <p className="muted small">
        {a.flagged} day{a.flagged === 1 ? "" : "s"} in this period had an unusual own move. About{" "}
        {a.expected.toFixed(1)} would be flagged by chance alone at the {Math.round(daily.alpha * 100)}% level, so{" "}
        {a.flagged > a.expected * 1.5 ? "this period had more surprises than luck explains." : "this is roughly what luck alone produces."}
      </p>
    </section>
  );
}
