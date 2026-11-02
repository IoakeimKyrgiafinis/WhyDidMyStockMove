import { useState } from "react";
import { api, type Headline, type HeadlineGroup, type HeadlineReaction } from "../api";
import { num, pct, pText, useAsync } from "../lib";
import { ReactionChart } from "./Charts";
import { Info } from "./Info";

const TIME_FMT = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "numeric",
  minute: "2-digit",
});

function nyTime(published: string): string {
  // `published` is already New York wall-clock time without a zone.
  const [hh, mm] = published.slice(11, 16).split(":").map(Number);
  const suffix = hh >= 12 ? "PM" : "AM";
  return `${((hh + 11) % 12) + 1}:${String(mm).padStart(2, "0")} ${suffix}`;
}

/** Headlines of the day, grouped, each with how the stock reacted beyond market and sector. */
export function HeadlineReactions({ ticker, date, headlines }: { ticker: string; date: string; headlines: Headline[] }) {
  const result = useAsync(headlines.length ? `${ticker}/${date}/reactions` : null, (signal) =>
    api.reactions(ticker, date, signal),
  );
  if (headlines.length === 0) return null;

  // Until reactions arrive (or if they are unavailable) show the plain headlines.
  if (result.state !== "ready" || !result.data.available) {
    return (
      <>
        {result.state === "loading" && <p className="muted small">Measuring the reaction to each headline...</p>}
        {result.state === "ready" && result.data.message && <p className="muted small">{result.data.message}</p>}
        {result.state === "error" && <p className="muted small">Reactions unavailable: {result.error.message}</p>}
        {headlines.slice(0, 12).map((h) => (
          <HeadlineLink key={h.url + h.created_at} h={h} />
        ))}
      </>
    );
  }

  const { groups, expected_false_flags, alpha } = result.data;
  const flagged = groups.filter((g) => g.reaction?.flagged).length;
  return (
    <>
      <div className="card__head">
        <span className="muted small">Reaction to each headline, beyond market and sector</span>
        <Info label="headline reactions">
          <p>
            The same market-and-sector model, run on 5-minute prices with sensitivities from the previous weeks only.
            The open and close are much more volatile than midday, so each move is scored against the usual noise of
            that time of day, and compared with the same half hour on earlier days.
          </p>
          <p>
            The move in the 30 minutes before the headline is shown too: many headlines are written after the price has
            already moved. Headlines outside market hours are scored on the move from the previous close to the next
            open. Headlines within 15 minutes of each other cannot be told apart and share one result.
          </p>
          <p>
            Scoring many headlines produces some unusual results by chance: about {expected_false_flags.toFixed(1)} of
            these {groups.length} would be flagged at the {Math.round(alpha * 100)}% level by luck alone.
          </p>
        </Info>
      </div>
      {groups.map((g) => (
        <GroupView key={g.published + g.headlines[0]?.url} group={g} />
      ))}
      {groups.length > 1 && (
        <p className="muted small">
          {flagged} of {groups.length} flagged; about {expected_false_flags.toFixed(1)} expected by chance.
        </p>
      )}
    </>
  );
}

function HeadlineLink({ h }: { h: Headline }) {
  return (
    <a className="item item--news" href={h.url} target="_blank" rel="noreferrer">
      <span className="item__kind">{h.source}</span>
      <span className="item__text">{h.headline}</span>
      <span className="item__meta">{TIME_FMT.format(new Date(`${h.created_at}Z`))} ET</span>
    </a>
  );
}

function GroupView({ group }: { group: HeadlineGroup }) {
  const [open, setOpen] = useState(false);
  const r = group.reaction;
  return (
    <div className={`reaction${r?.flagged ? " reaction--flag" : ""}`}>
      <div className="reaction__head">
        <span className="muted small">{r?.kind === "overnight" ? "Outside market hours" : nyTime(group.published) + " ET"}</span>
        {r ? (
          <button type="button" className={`rchip${r.flagged ? " rchip--flag" : ""}`} onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            <ChipText r={r} />
          </button>
        ) : (
          <span className="muted small">{group.error}</span>
        )}
      </div>
      {r?.moved_before && r.before && (
        <p className="reaction__warn small">
          The stock had already moved {pct(r.before.abnormal)} beyond expected
          {r.before.z !== null ? ` (${num(r.before.z, 1)}σ)` : ""} in the {r.before.minutes} minutes before. This headline may
          be reporting the move, not causing it.
        </p>
      )}
      {open && r && <ReactionDetail r={r} />}
      {group.headlines.map((h) => (
        <HeadlineLink key={h.url + h.created_at} h={h} />
      ))}
    </div>
  );
}

function ChipText({ r }: { r: HeadlineReaction }) {
  const span = r.kind === "overnight" ? "overnight" : `in ${r.after.minutes} min`;
  return (
    <>
      <span className={r.after.abnormal >= 0 ? "pos" : "neg"}>{pct(r.after.abnormal)}</span> beyond expected {span}
      {r.after.z !== null && <span className="muted"> · {num(Math.abs(r.after.z), 1)}σ</span>}
      {r.flagged && <strong> · unusual</strong>}
    </>
  );
}

function ReactionDetail({ r }: { r: HeadlineReaction }) {
  return (
    <div className="reaction__detail">
      {r.path ? (
        <ReactionChart minutes={r.path.minutes} actual={r.path.actual} expected={r.path.expected} band={r.path.band} />
      ) : (
        <p className="muted small">
          Measured from the previous close to the first five minutes of the {r.session} session, against what the market
          and sector did over the same gap.
        </p>
      )}
      <dl className="facts">
        <div>
          <dt>Before the headline</dt>
          <dd className="num">
            {r.before ? `${pct(r.before.abnormal)}${r.before.z !== null ? ` (${num(r.before.z, 1)}σ)` : ""}` : "n/a"}
          </dd>
        </div>
        <div>
          <dt>After the headline</dt>
          <dd className="num">
            {pct(r.after.abnormal)}
            {r.after.z !== null ? ` (${num(r.after.z, 1)}σ)` : ""}
          </dd>
        </div>
        <div>
          <dt>Against earlier days</dt>
          <dd className="num">
            {r.p_empirical !== null ? `${pText(r.p_empirical)} (${r.n_reference} days)` : "not enough history"}
          </dd>
        </div>
      </dl>
    </div>
  );
}
