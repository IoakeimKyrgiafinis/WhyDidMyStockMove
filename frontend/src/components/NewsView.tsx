import { api, type DayDetail } from "../api";
import { fmtDate, money, num, pct, titleCase, useAsync } from "../lib";
import { HeadlineReactions } from "./HeadlineReactions";
import { Info } from "./Info";

/** The second view: everything that became public in time for one session, with room to read it. */
export function NewsView({ ticker, date, onBack }: { ticker: string; date: string; onBack: () => void }) {
  const result = useAsync(`${ticker}/${date}`, (signal) => api.day(ticker, date, signal));

  return (
    <div className="newsview">
      <header className="newsview__head">
        <button type="button" className="pill pill--ghost" onClick={onBack}>
          ← Back to the overview
        </button>
        <div>
          <h1 className="newsview__title">What else happened on {fmtDate(date)}</h1>
          <p className="muted">
            Headlines, company filings and insider trades that became public in time for this session.
          </p>
        </div>
      </header>
      {result.state === "loading" && (
        <div className="status">
          <div className="spinner" aria-hidden="true" />
          <p>Gathering the day's news...</p>
        </div>
      )}
      {result.state === "error" && <p className="error">{result.error.message}</p>}
      {result.state === "ready" && <NewsBody day={result.data} />}
    </div>
  );
}

function NewsBody({ day }: { day: DayDetail }) {
  const { headlines, filings, insiders } = day.items;
  return (
    <>
      <dl className="newsview__summary">
        <div>
          <dt>Move</dt>
          <dd className={`num ${day.total >= 0 ? "pos" : "neg"}`}>{pct(day.total)}</dd>
        </div>
        <div>
          <dt>Market and sector expected</dt>
          <dd className="num">{pct(day.market + day.sector + day.drift)}</dd>
        </div>
        <div>
          <dt>Unexplained</dt>
          <dd className={`num ${day.unexplained >= 0 ? "pos" : "neg"}`}>{pct(day.unexplained)}</dd>
        </div>
        <div>
          <dt>Unusual-move score</dt>
          <dd className="num">
            {day.z === null ? "n/a" : `${day.z > 0 ? "+" : ""}${num(day.z, 1)}σ`}
            {day.flagged && <span className="badge badge--poor">Unusual</span>}
          </dd>
        </div>
      </dl>

      <div className="newsview__grid">
        <section className="card">
          <header className="card__head">
            <h2>Headlines · {headlines.length}</h2>
            <Info label="timing">
              <p>
                Items are placed on the first session that could react to them. A headline or filing after the 4pm New
                York close counts toward the next day. Insider filings count from the day after they were filed, since
                the bulk data has no filing time.
              </p>
            </Info>
          </header>
          {headlines.length === 0 ? (
            <p className="muted">No headlines for this day.</p>
          ) : (
            <div className="newsview__list">
              <HeadlineReactions ticker={day.ticker} date={day.date} headlines={headlines} />
            </div>
          )}
        </section>

        <div className="newsview__side">
          <section className="card">
            <header className="card__head">
              <h2>Company filings · {filings.length}</h2>
            </header>
            {filings.length === 0 && <p className="muted small">No 8-K filings for this day.</p>}
            <div className="newsview__list">
              {filings.map((f) => (
                <a key={f.accession} className="item item--filing" href={f.url} target="_blank" rel="noreferrer">
                  <span className="item__kind">{f.earnings ? "Earnings 8-K" : "8-K"}</span>
                  <span className="item__text">
                    {f.labels.filter((l) => !l.startsWith("Financial statements")).join("; ") || "Company filing"}
                  </span>
                  {f.accepted_at && (
                    <span className="item__meta">
                      filed {fmtDate(f.accepted_at.slice(0, 10))} {f.accepted_at.slice(11, 16)}
                    </span>
                  )}
                </a>
              ))}
            </div>
          </section>

          <section className="card">
            <header className="card__head">
              <h2>Insider filings · {insiders.length}</h2>
            </header>
            {insiders.length === 0 && <p className="muted small">No insider trades filed for this day.</p>}
            <div className="newsview__list">
              {insiders.map((i) => (
                <a key={i.accession} className={`item item--${i.signal}`} href={i.url} target="_blank" rel="noreferrer">
                  <span className="item__kind">{i.signal_label}</span>
                  <span className="item__text">
                    {titleCase(i.owner_name)} ({i.owner_role}){i.value !== null ? `, ${money(i.value)}` : ""}
                  </span>
                  <span className="item__meta">filed {fmtDate(i.filing_date)}</span>
                </a>
              ))}
            </div>
          </section>

          <p className="muted small">
            Unexplained is not the same as news: fund flows, style effects and plain noise land there too. A coinciding
            headline shows timing, not cause.
          </p>
        </div>
      </div>
    </>
  );
}
