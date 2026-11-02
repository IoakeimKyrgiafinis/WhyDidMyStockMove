import type { Interval, Profile } from "../api";
import { num, pct, pValue } from "../lib";
import { Info } from "./Info";

const STATUS_TEXT = {
  good: "Good fit",
  caution: "Use with caution",
  poor: "Poor fit",
} as const;

export function HealthCard({ profile }: { profile: Profile }) {
  const { health: h, variance: v } = profile;
  const shares: { key: string; label: string; value: Interval }[] = [
    { key: "market", label: "Market", value: v.market },
    { key: "sector", label: "Sector", value: v.sector },
    { key: "own", label: "Own", value: v.own },
  ];

  return (
    <section className="card">
      <header className="card__head">
        <h2>How much of this stock is just the market?</h2>
        <Info label="variance split">
          <p>
            The share of the day-to-day ups and downs over the last {profile.window} trading days that came from the
            market, the sector and the stock itself. The three shares add to 100%.
          </p>
          <p>
            The ranges are {Math.round(v.level * 100)}% bootstrap intervals: the data was resampled {v.n_boot} times
            in {v.block}-day blocks, which keeps calm and turbulent stretches together.
          </p>
        </Info>
      </header>

      <div className="shares">
        {shares.map((s) => (
          <div className="shares__row" key={s.key}>
            <span className="contrib__label">
              <span className={`swatch swatch--${s.key}`} />
              {s.label}
            </span>
            <span className="shares__bar">
              <span className={`shares__fill contrib__fill--${s.key}`} style={{ width: `${s.value.estimate * 100}%` }} />
              <span
                className="shares__ci"
                style={{ left: `${s.value.low * 100}%`, width: `${Math.max(0.5, (s.value.high - s.value.low) * 100)}%` }}
              />
            </span>
            <span className="num shares__value">
              {pct(s.value.estimate, 0, false)}
              <span className="shares__range">
                {pct(s.value.low, 0, false)}–{pct(s.value.high, 0, false)}
              </span>
            </span>
          </div>
        ))}
      </div>

      <div className="health">
        <div className="card__head">
          <h3>
            Model health <span className={`badge badge--${h.status}`}>{STATUS_TEXT[h.status]}</span>
          </h3>
          <Info label="model health">
            <p>
              The badge reflects R-squared, whether the betas changed between the two halves of the year, and whether
              the leftover moves are autocorrelated. The normality and volatility-clustering tests are shown for
              information only: daily stock returns almost always fail them, so counting them would flag every stock.
            </p>
          </Info>
        </div>
        {h.reasons.length > 0 && (
          <ul className="reasons">
            {h.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        <dl className="facts">
          <div>
            <dt>Market beta</dt>
            <dd className="num">
              {num(profile.beta_market.estimate)} <span className="muted">± {num(1.96 * profile.beta_market.se)}</span>
            </dd>
          </div>
          <div>
            <dt>Sector beta</dt>
            <dd className="num">
              {num(profile.beta_sector.estimate)} <span className="muted">± {num(1.96 * profile.beta_sector.se)}</span>
            </dd>
          </div>
          <div>
            <dt>R-squared</dt>
            <dd className="num">{num(h.r_squared)}</dd>
          </div>
          <div>
            <dt>Beta stability (z)</dt>
            <dd className="num">
              {num(h.beta_z_market, 1)} / {num(h.beta_z_sector, 1)}
            </dd>
          </div>
          <div>
            <dt>Autocorrelation (Ljung-Box p)</dt>
            <dd className="num">{pValue(h.ljung_box_p)}</dd>
          </div>
          <div>
            <dt>Fat tails (excess kurtosis)</dt>
            <dd className="num">{num(h.excess_kurtosis, 1)}</dd>
          </div>
          <div>
            <dt>Normality (Jarque-Bera p)</dt>
            <dd className="num">{pValue(h.jarque_bera_p)}</dd>
          </div>
          <div>
            <dt>Volatility clustering (ARCH p)</dt>
            <dd className="num">{pValue(h.arch_lm_p)}</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}
