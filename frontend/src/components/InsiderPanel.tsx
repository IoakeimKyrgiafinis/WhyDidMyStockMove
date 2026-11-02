import { useState } from "react";
import type { CarTest, Insiders, PooledStudy, Signal } from "../api";
import { fmtDate, money, num, pct, pText, pValue, titleCase } from "../lib";
import { EventPathChart } from "./Charts";
import { Info } from "./Info";

const WINDOW_NAMES: Record<string, string> = { "0-1": "Day 0 to +1", "0-5": "First week", "0-20": "First month" };

const FILTERS: { key: "all" | Signal; label: string }[] = [
  { key: "all", label: "All" },
  { key: "buy", label: "Purchases" },
  { key: "sale", label: "Sales" },
  { key: "planned_sale", label: "Planned sales" },
  { key: "routine", label: "Routine" },
];

export function EarningsCard({ data }: { data: Insiders }) {
  const e = data.earnings;
  if (!e) return null;
  const strong = e.p_value < 0.05;
  return (
    <section className="card">
      <header className="card__head">
        <h2>Earnings days</h2>
        <Info label="earnings reaction">
          <p>
            Days when the company filed its results (8-K Item 2.02) are compared with ordinary days. The measure is
            the size of the unexplained move, scaled by recent volatility, ignoring its direction. The p-value comes
            from {e.n_permutations.toLocaleString()} random sets of the same number of ordinary days.
          </p>
        </Info>
      </header>
      <p className="lede">
        On its {e.n_events} earnings days, {data.ticker}'s unexplained move was{" "}
        <strong>{num(e.ratio, 1)}×</strong> the size of a typical day's.
      </p>
      <p className="muted small">
        Average size {num(e.mean_abs_z_events, 2)}σ against {num(e.mean_abs_z_all, 2)}σ on all days ({pText(e.p_value)}). {strong ? "Results days clearly move this stock more than other days." : "That is not clearly different from ordinary days."}
      </p>
    </section>
  );
}

export function InsiderPanel({ data }: { data: Insiders }) {
  const [filter, setFilter] = useState<"all" | Signal>("all");
  const [limit, setLimit] = useState(15);
  const rows = data.filings.filter((f) => filter === "all" || f.signal === filter);

  return (
    <section className="card">
      <header className="card__head">
        <h2>Insider trades</h2>
        <Info label="insider trades">
          <p>
            Officers, directors and 10% owners must report trades in their own company's stock on SEC Form 4 within
            two business days. These are legal, after-the-fact disclosures, not early warnings, and nothing here
            detects illegal trading.
          </p>
          <p>
            Open-market purchases are the informative ones: insiders buy for one reason. Sales happen for many
            reasons (taxes, diversification), and sales under a 10b5-1 plan were scheduled months in advance. Awards,
            option exercises and tax withholding carry almost no information.
          </p>
        </Info>
      </header>

      {data.filings.length === 0 ? (
        <p className="muted">No Form 4 filings for this stock in the data.</p>
      ) : (
        <>
          <div className="filters" role="group" aria-label="Filter by trade type">
            {FILTERS.map((f) => {
              const count = f.key === "all" ? data.filings.length : (data.counts[f.key] ?? 0);
              if (f.key !== "all" && count === 0) return null;
              return (
                <button
                  key={f.key}
                  type="button"
                  className={`chip${filter === f.key ? " is-active" : ""}`}
                  onClick={() => {
                    setFilter(f.key);
                    setLimit(15);
                  }}
                >
                  {f.label} <span className="muted">{count}</span>
                </button>
              );
            })}
          </div>
          <div className="table-wrap">
            <table className="trades">
              <thead>
                <tr>
                  <th>Filed</th>
                  <th>Insider</th>
                  <th>Type</th>
                  <th className="num">Value</th>
                  <th className="num">Price</th>
                  <th className="num" title="Calendar days from the last trade to the filing">
                    Lag
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, limit).map((f) => (
                  <tr key={f.accession} className={`trades__row trades__row--${f.signal}`}>
                    <td>
                      <a href={f.url} target="_blank" rel="noreferrer">
                        {fmtDate(f.filing_date)}
                      </a>
                    </td>
                    <td>
                      {titleCase(f.owner_name)}
                      <span className="muted small"> · {f.owner_role}</span>
                    </td>
                    <td>
                      <span className={`tag tag--${f.signal}`}>{f.signal_label}</span>
                    </td>
                    <td className="num">{f.value !== null ? money(f.value) : "–"}</td>
                    <td className="num">{f.price !== null ? `$${f.price.toFixed(2)}` : "–"}</td>
                    <td className="num">{f.lag_days !== null ? `${f.lag_days}d` : "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > limit && (
            <button type="button" className="link" onClick={() => setLimit((l) => l + 30)}>
              Show more ({rows.length - limit} left)
            </button>
          )}
        </>
      )}

      <StockStudySection data={data} />
      <PooledSection data={data} />
    </section>
  );
}

function StockStudySection({ data }: { data: Insiders }) {
  const s = data.study;
  const buys = data.counts.buy ?? 0;
  return (
    <div className="study">
      <header className="card__head">
        <h3>After insiders bought {data.ticker}</h3>
        <Info label="insider event study">
          <p>
            For each purchase, the stock's unexplained daily moves are added up from the first day the filing was
            public. Starting from the trade date would use information nobody had yet. Overlapping purchases count
            once, and the band is a 95% confidence interval.
          </p>
          <p>
            The permutation test compares the first-week result with {s?.permutation?.n_permutations.toLocaleString() ?? "thousands of"}{" "}
            random sets of the same number of ordinary days, with no assumption about the shape of returns.
          </p>
        </Info>
      </header>
      {!s || s.n_used < 2 ? (
        <p className="muted small">
          {buys === 0
            ? "No open-market purchases by insiders in the data."
            : `Only ${s?.n_used ?? 0} usable purchase${(s?.n_used ?? 0) === 1 ? "" : "s"} (of ${buys}), too few to test for one stock. The study across all stocks below has the statistical power that a single stock lacks.`}
        </p>
      ) : (
        <>
          <EventPathChart
            days={s.relative_days}
            mean={s.mean_car}
            low={s.ci_low}
            high={s.ci_high}
            label="Average cumulative unexplained return around insider purchases"
          />
          <TestTable tests={s.tests} />
          {s.permutation && (
            <p className="muted small">
              First week after {s.n_used} purchases: {pct(s.permutation.observed)} on average, against{" "}
              {pct(s.permutation.null_mean)} ± {pct(s.permutation.null_std, 2, false)} for random days (permutation{" "}
              {pText(s.permutation.p_value)}).
              {s.dropped_overlap + s.dropped_incomplete > 0 &&
                ` ${s.dropped_overlap + s.dropped_incomplete} purchases were left out: overlapping or too close to the edge of the data.`}
            </p>
          )}
        </>
      )}
    </div>
  );
}

function PooledSection({ data }: { data: Insiders }) {
  const pooled = data.pooled;
  const [which, setWhich] = useState<"buy" | "sale" | "planned_sale">("buy");
  if (!pooled) return null;
  const study: PooledStudy | undefined = pooled.studies[which];
  const names = { buy: "Purchases", sale: "Sales", planned_sale: "Planned sales" } as const;
  return (
    <div className="study">
      <header className="card__head">
        <h3>Across all stocks</h3>
        <Info label="pooled insider study">
          <p>
            The same study across every stock in the universe, rebuilt each night. Insiders often file on the same
            days, and those events share market shocks, so all events on one date are averaged first and the test
            runs across dates. Each date counts once.
          </p>
          <p>Academic studies generally find purchases followed by positive abnormal returns and sales by much weaker effects. These numbers are whatever this data says.</p>
        </Info>
      </header>
      <div className="filters" role="group" aria-label="Trade type">
        {(Object.keys(names) as (keyof typeof names)[])
          .filter((k) => pooled.studies[k])
          .map((k) => (
            <button key={k} type="button" className={`chip${which === k ? " is-active" : ""}`} onClick={() => setWhich(k)}>
              {names[k]}
            </button>
          ))}
      </div>
      {study ? (
        <>
          <EventPathChart
            days={study.relative_days}
            mean={study.mean_car}
            low={study.ci_low}
            high={study.ci_high}
            label="Average cumulative unexplained return around insider trades, all stocks"
          />
          <TestTable tests={study.tests} />
          <p className="muted small">
            {study.n_events.toLocaleString()} filings in {study.n_stocks.toLocaleString()} stocks on{" "}
            {study.n_clusters.toLocaleString()} dates · as of {fmtDate(pooled.as_of)}
          </p>
        </>
      ) : (
        <p className="muted small">Not enough events of this type.</p>
      )}
    </div>
  );
}

function TestTable({ tests }: { tests: CarTest[] }) {
  return (
    <table className="parts tests">
      <thead>
        <tr>
          <th>Window</th>
          <th className="num">Average</th>
          <th className="num">95% interval</th>
          <th className="num">p</th>
        </tr>
      </thead>
      <tbody>
        {tests.map((t) => {
          const lo = t.ci_low ?? null;
          const hi = t.ci_high ?? null;
          const significant = t.p_value !== null && t.p_value < 0.05;
          return (
            <tr key={`${t.start}-${t.end}`}>
              <th scope="row">{WINDOW_NAMES[`${t.start}-${t.end}`] ?? `Day ${t.start} to ${t.end}`}</th>
              <td className={`num ${significant ? (t.mean_car > 0 ? "pos" : "neg") : ""}`}>{pct(t.mean_car)}</td>
              <td className="num muted">{lo !== null && hi !== null ? `${pct(lo, 1)} to ${pct(hi, 1)}` : "–"}</td>
              <td className="num">{t.p_value !== null ? pValue(t.p_value) : "–"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
