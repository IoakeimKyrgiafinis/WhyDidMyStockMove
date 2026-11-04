import { api, type DayDetail, type Filing, type MacroRelease } from "../api";
import { fmtDate, fmtNewYork, money, num, pct, titleCase, useAsync } from "../lib";
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
            Economic releases, headlines, company filings and insider trades that became public in time for this session.
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
  const { headlines, filings, insiders, macro } = day.items;
  const announcements = filings.filter((f) => f.category === "current");
  const others = filings.filter((f) => f.category !== "current");
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
        <div className="newsview__main">
          {announcements.length > 0 && (
            <section className="card">
              <header className="card__head">
                <h2>Company announcements · {announcements.length}</h2>
              </header>
              <div className="newsview__list">
                {announcements.map((f) => (
                  <Announcement key={f.accession} filing={f} />
                ))}
              </div>
            </section>
          )}
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
        </div>

        <div className="newsview__side">
          {macro.length > 0 && <Economy releases={macro} market={day.market} />}
          <section className="card">
            <header className="card__head">
              <h2>Other SEC filings · {others.length}</h2>
              <Info label="other filings">
                <p>
                  Ownership stakes (13D for activists and buyers of control, 13G for passive holders), share
                  offerings, tender offers, merger votes and periodic reports. Routine ones are dimmed and get no
                  marker on the chart.
                </p>
              </Info>
            </header>
            {others.length === 0 && <p className="muted small">No other SEC filings for this day.</p>}
            <div className="newsview__list">
              {others.map((f) => (
                <a
                  key={f.accession}
                  className={`item item--${f.category}${f.minor ? " item--minor" : ""}`}
                  href={f.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  <span className="item__kind">{f.kind_label}</span>
                  <span className="item__text muted">Form {f.form}</span>
                  <FiledAt filing={f} />
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

/** An 8-K or 6-K: the attached press release when there is one, otherwise its item list. */
function Announcement({ filing: f }: { filing: Filing }) {
  const kind = f.earnings ? "Earnings 8-K" : f.form === "6-K" ? "6-K" : "8-K";
  const items = f.labels.filter((l) => !l.startsWith("Financial statements")).join("; ");
  return (
    <article className={`announcement${f.earnings ? " announcement--earnings" : ""}`}>
      <header className="announcement__head">
        <span className="tag">{kind}</span>
        <FiledAt filing={f} />
      </header>
      {f.press ? (
        <>
          <h3 className="announcement__title">{f.press.title}</h3>
          <p className="announcement__summary">{f.press.summary}</p>
        </>
      ) : (
        <h3 className="announcement__title">{items || "Company filing"}</h3>
      )}
      <footer className="announcement__links">
        {f.press && (
          <a href={f.press.url} target="_blank" rel="noreferrer">
            Read the press release
          </a>
        )}
        <a href={f.url} target="_blank" rel="noreferrer">
          The filing on EDGAR
        </a>
        {f.press && items && <span className="muted small">{items}</span>}
      </footer>
    </article>
  );
}

function FiledAt({ filing: f }: { filing: Filing }) {
  if (!f.accepted_at) return <span className="item__meta">filed {fmtDate(f.filing_date)}</span>;
  return (
    <span className="item__meta">
      filed {fmtNewYork(f.accepted_at)}
    </span>
  );
}

/** A FRED value as shown: "+0.4%", "+142k jobs", "231k", "7.23M". */
function macroValue(r: MacroRelease, v: number): string {
  const sign = r.signed && v > 0 ? "+" : "";
  const text = `${sign}${v.toFixed(r.decimals)}`;
  return r.unit === "%" || r.unit === "k" || r.unit === "M" ? `${text}${r.unit}` : `${text} ${r.unit}`;
}

function fedRange(upper: number): string {
  return `${(upper - 0.25).toFixed(2)}–${upper.toFixed(2)}%`;
}

function fedChange(r: MacroRelease): string {
  if (r.previous === null) return "";
  const bp = Math.round((r.value - r.previous) * 100);
  if (bp === 0) return "held";
  return bp < 0 ? `cut by ${-bp} bp` : `raised by ${bp} bp`;
}

function releaseTime(t: string): string {
  const [hh, mm] = t.split(":").map(Number);
  return `${((hh + 11) % 12) + 1}:${String(mm).padStart(2, "0")} ${hh >= 12 ? "PM" : "AM"} ET`;
}

/** Releases that move the whole market: they show up in the market part of the move. */
function Economy({ releases, market }: { releases: MacroRelease[]; market: number }) {
  return (
    <section className="card">
      <header className="card__head">
        <h2>The economy that day · {releases.length}</h2>
        <Info label="economic releases">
          <p>
            Figures as first published, before later revisions, from FRED (St. Louis Fed). They move the whole market,
            so their effect shows up in the market part of the move ({pct(market)} for this stock today), not in the
            unexplained part.
          </p>
        </Info>
      </header>
      <div className="newsview__list">
        {releases.map((r) => (
          <a key={r.key} className="macro" href={r.url} target="_blank" rel="noreferrer" title={r.about}>
            <span className="macro__name">{r.name}</span>
            <span className="macro__value num">
              {r.key === "fomc" ? fedRange(r.value) : macroValue(r, r.value)}
            </span>
            <span className="macro__meta">
              {r.key === "fomc"
                ? `${fedChange(r)} · ${releaseTime(r.time_et)}`
                : `${r.period}${r.previous !== null ? ` · before ${macroValue(r, r.previous)}` : ""} · ${releaseTime(r.time_et)}`}
            </span>
          </a>
        ))}
      </div>
    </section>
  );
}
