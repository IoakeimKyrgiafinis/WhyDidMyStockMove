import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Search } from "./components/Search";
import { StockView } from "./components/StockView";
import { fmtDate, readRoute, useAsync, writeRoute, type Route } from "./lib";

const EXAMPLES = ["AAPL", "NVDA", "JPM", "XOM", "TSLA", "UNH"];

export default function App() {
  const [route, setRoute] = useState<Route>(readRoute);

  useEffect(() => {
    const onPop = () => setRoute(readRoute());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const go = useCallback((next: Route, replace = false) => {
    writeRoute(next, replace);
    setRoute(next);
    if (!replace) window.scrollTo({ top: 0 });
  }, []);

  useEffect(() => {
    document.title = route.ticker ? `${route.ticker} · Why did it move?` : "Why did my stock move?";
  }, [route.ticker]);

  return (
    <div className="app">
      <header className="topbar">
        <a
          className="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            go({ ticker: null, day: null });
          }}
        >
          Why did my stock move?
        </a>
        {route.ticker && <Search onSelect={(t) => go({ ticker: t, day: null })} />}
      </header>

      <main>
        {route.ticker ? (
          <StockView
            ticker={route.ticker}
            day={route.day}
            onDay={(day) => go({ ticker: route.ticker, day }, true)}
            onTicker={(t) => go({ ticker: t, day: null })}
          />
        ) : (
          <Landing onSelect={(t) => go({ ticker: t, day: null })} />
        )}
      </main>

      <Footer />
    </div>
  );
}

function Landing({ onSelect }: { onSelect: (ticker: string) => void }) {
  const meta = useAsync("meta", (signal) => api.meta(signal));

  return (
    <div className="landing">
      <h1>Why did my stock move?</h1>
      <p className="lede">
        Split any US stock's move into what the market did, what its sector did, and what is left over for the
        company itself, with statistics that say how much to trust each number.
      </p>
      <Search onSelect={onSelect} autoFocus large />
      <div className="examples">
        <span className="muted small">Try</span>
        {EXAMPLES.map((t) => (
          <button key={t} type="button" className="chip" onClick={() => onSelect(t)}>
            {t}
          </button>
        ))}
      </div>
      <p className="muted small">
        {meta.state === "ready" && meta.data.status === "ready" && meta.data.as_of
          ? `${meta.data.n_stocks.toLocaleString()} stocks from the Russell 3000, prices to ${fmtDate(meta.data.as_of)}.`
          : meta.state === "ready"
            ? meta.data.message ?? "The data is loading."
            : meta.state === "error"
              ? meta.error.message
              : "Connecting..."}
      </p>

      <div className="how">
        <div>
          <h3>Out of sample</h3>
          <p>Each day is explained with sensitivities estimated on the year before it, never with later data.</p>
        </div>
        <div>
          <h3>Honest uncertainty</h3>
          <p>Newey-West error bands, bootstrap intervals and model-health checks come with every number.</p>
        </div>
        <div>
          <h3>Fat tails respected</h3>
          <p>Unusual moves are judged against the stock's own history, not a bell curve.</p>
        </div>
      </div>
    </div>
  );
}

function Footer() {
  return (
    <footer className="footer">
      <p>
        Descriptive, not predictive, and not investment advice. Unexplained is not the same as news: flows, style
        effects and noise land there too. The two-factor model fits large caps better than small caps. Free price data
        is limited, and stocks with incomplete history are left out. The universe is today's index, so it carries
        survivorship bias. Correlations and betas drift over time.
      </p>
      <p>Prices: Alpaca (IEX). Universe: iShares Russell 3000 ETF holdings. US-listed stocks only.</p>
    </footer>
  );
}
