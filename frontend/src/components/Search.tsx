import { useEffect, useId, useRef, useState } from "react";
import { api, type StockRef } from "../api";
import { titleCase } from "../lib";

interface Props {
  onSelect: (ticker: string) => void;
  autoFocus?: boolean;
  large?: boolean;
}

export function Search({ onSelect, autoFocus = false, large = false }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<StockRef[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const q = query.trim();
    if (!q) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api.search(q, controller.signal).then(
        (found) => {
          setResults(found);
          setActive(0);
        },
        () => setResults([]),
      );
    }, 120);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query]);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const choose = (stock: StockRef) => {
    setQuery("");
    setResults([]);
    setOpen(false);
    onSelect(stock.ticker);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      const pick = results[active];
      if (pick) choose(pick);
      else if (query.trim()) {
        onSelect(query.trim().toUpperCase());
        setQuery("");
      }
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  };

  const showList = open && query.trim() !== "" && results.length > 0;

  return (
    <div className={`search${large ? " search--large" : ""}`} ref={box}>
      <input
        type="search"
        placeholder="Search a US stock, e.g. NVDA or Apple"
        value={query}
        autoFocus={autoFocus}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-label="Search stocks"
      />
      {showList && (
        <ul className="search__list" id={listId} role="listbox">
          {results.map((stock, i) => (
            <li
              key={stock.ticker}
              role="option"
              aria-selected={i === active}
              className={i === active ? "is-active" : undefined}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(stock);
              }}
            >
              <span className="search__ticker">{stock.ticker}</span>
              <span className="search__name">{titleCase(stock.name)}</span>
              <span className="search__sector">{stock.sector}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
