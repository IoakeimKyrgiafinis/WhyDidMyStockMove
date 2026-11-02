import { useEffect, useRef } from "react";
import { api, type DayDetail } from "../api";
import { fmtDate, money, num, pct, pValue, rarity, titleCase, useAsync } from "../lib";
import { HeadlineReactions } from "./HeadlineReactions";
import { Info } from "./Info";

const Z95 = 1.96;

export function DayPanel({ ticker, date, onClose }: { ticker: string; date: string; onClose: () => void }) {
  const result = useAsync(`${ticker}/${date}`, (signal) => api.day(ticker, date, signal));
  const panel = useRef<HTMLElement>(null);

  // On narrow screens the panel sits below the charts: bring it into view when a day is picked.
  useEffect(() => {
    if (window.matchMedia("(max-width: 960px)").matches) {
      panel.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [date]);

  return (
    <section className="card day" aria-live="polite" ref={panel}>
      <header className="card__head">
        <h2>{fmtDate(date)}</h2>
        <button type="button" className="link" onClick={onClose}>
          Close
        </button>
      </header>
      {result.state === "loading" && <p className="muted">Explaining the day...</p>}
      {result.state === "error" && <p className="error">{result.error.message}</p>}
      {result.state === "ready" && <DayBody day={result.data} />}
    </section>
  );
}

function DayBody({ day }: { day: DayDetail }) {
  const explained = day.market + day.sector + day.drift;
  const parts = [
    { key: "market", label: "Market", value: day.market, se: day.se_market },
    { key: "sector", label: "Sector", value: day.sector, se: day.se_sector },
    { key: "drift", label: "Baseline drift", value: day.drift, se: null },
    { key: "own", label: "Unexplained", value: day.unexplained, se: null },
  ];

  return (
    <>
      <p className="lede">
        {day.ticker} {day.total >= 0 ? "rose" : "fell"}{" "}
        <strong className={day.total >= 0 ? "pos" : "neg"}>{pct(Math.abs(day.total), 2, false)}</strong>. Given what
        the market and its sector did, the model expected {pct(explained)} (95% band{" "}
        {pct(explained - Z95 * day.se_explained)} to {pct(explained + Z95 * day.se_explained)}), leaving{" "}
        <strong>{pct(day.unexplained)}</strong> unexplained.
      </p>

      <table className="parts">
        <tbody>
          {parts.map((p) => (
            <tr key={p.key}>
              <th scope="row">
                <span className={`swatch swatch--${p.key === "drift" ? "drift" : p.key}`} />
                {p.label}
              </th>
              <td className="num">{pct(p.value)}</td>
              <td className="num muted">{p.se !== null ? `± ${pct(Z95 * p.se, 2, false)}` : ""}</td>
            </tr>
          ))}
          <tr className="parts__total">
            <th scope="row">Total</th>
            <td className="num">{pct(day.total)}</td>
            <td />
          </tr>
        </tbody>
      </table>
      <p className="muted small">
        Betas used: market {num(day.beta_market)}, sector {num(day.beta_sector)}, estimated on the {day.window}{" "}
        trading days before this day only.
      </p>

      <div className="score">
        <header className="card__head">
          <h3>Was the unexplained part unusual?</h3>
          <Info label="unusual-move score">
            <p>
              The unexplained move is divided by this stock's recent unexplained volatility (an exponentially weighted
              average of earlier days, never including the day itself). That puts jumpy and calm stocks on the same
              scale.
            </p>
            <p>
              The probability is empirical: the share of the previous 250 days with a move at least this large. A bell
              curve would claim big moves are rarer than they really are, so its figure is shown beside it for
              comparison.
            </p>
          </Info>
        </header>
        {day.z === null || day.p_value === null ? (
          <p className="muted">Not enough history yet to score this day.</p>
        ) : (
          <>
            <div className={`verdict${day.flagged ? " verdict--flag" : ""}`}>
              <span className="verdict__z num">{day.z > 0 ? "+" : ""}{num(day.z, 1)}σ</span>
              <span>
                {day.flagged ? "Unusual for this stock." : "Within this stock's normal range."}{" "}
                {rarity(day.return_period ?? 1 / day.p_value)}
              </span>
            </div>
            <dl className="facts">
              <div>
                <dt>Empirical probability</dt>
                <dd className="num">{pValue(day.p_value)}</dd>
              </div>
              <div>
                <dt>If returns were normal</dt>
                <dd className="num">{day.p_normal !== null ? pValue(day.p_normal) : "n/a"}</dd>
              </div>
              <div>
                <dt>Recent unexplained volatility</dt>
                <dd className="num">{day.sigma !== null ? pct(day.sigma, 2, false) : "n/a"} a day</dd>
              </div>
            </dl>
          </>
        )}
      </div>
      <DayItems day={day} />
      <p className="muted small">
        Unexplained is not the same as news: fund flows, style effects and plain noise land there too. A coinciding
        headline shows timing, not cause.
      </p>
    </>
  );
}

/** Headlines, 8-K filings and insider filings that became public in time for this session. */
function DayItems({ day }: { day: DayDetail }) {
  const { headlines, filings, insiders } = day.items;
  const empty = headlines.length + filings.length + insiders.length === 0;
  return (
    <div className="dayitems">
      <header className="card__head">
        <h3>What else happened</h3>
        <Info label="timing">
          <p>
            Items are placed on the first session that could react to them. A headline or filing after the 4pm New
            York close counts toward the next day. Insider filings count from the day after they were filed, since
            the bulk data has no filing time.
          </p>
        </Info>
      </header>
      {empty && <p className="muted small">No headlines, company filings or insider trades for this day.</p>}
      {filings.map((f) => (
        <a key={f.accession} className="item item--filing" href={f.url} target="_blank" rel="noreferrer">
          <span className="item__kind">{f.earnings ? "Earnings 8-K" : "8-K"}</span>
          <span className="item__text">{f.labels.filter((l) => !l.startsWith("Financial statements")).join("; ") || "Company filing"}</span>
          {f.accepted_at && (
            <span className="item__meta">
              filed {fmtDate(f.accepted_at.slice(0, 10))} {f.accepted_at.slice(11, 16)}
            </span>
          )}
        </a>
      ))}
      {insiders.map((i) => (
        <a key={i.accession} className={`item item--${i.signal}`} href={i.url} target="_blank" rel="noreferrer">
          <span className="item__kind">{i.signal_label}</span>
          <span className="item__text">
            {titleCase(i.owner_name)} ({i.owner_role}){i.value !== null ? `, ${money(i.value)}` : ""}
          </span>
          <span className="item__meta">filed {fmtDate(i.filing_date)}</span>
        </a>
      ))}
      <HeadlineReactions ticker={day.ticker} date={day.date} headlines={headlines} />
    </div>
  );
}
