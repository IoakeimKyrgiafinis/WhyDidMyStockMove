"""World press coverage from GDELT: which articles are about which company, and how to show them.

GDELT reads news sites worldwide and lists, for every article, its title and the organisations
it names. An article counts for a company when the title names it and GDELT found it among the
article's organisations (both are needed: "Apple" in a recipe headline is not Apple), or when
the title carries the company's exchange tag or cashtag ("(NASDAQ:NVDA)", "$NVDA").

Coverage is noisy, so nothing here feeds a statistic. The page shows how much coverage a day
had against the stock's usual day, and the stories carried most widely, ranked first by
whether the headline is about the business (shares, earnings, deals, analysts, lawsuits).
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import pandas as pd

from whymoved.domain.universe import UniverseMember

WORLD_COLUMNS = ["ticker", "seen_at", "title", "url", "domain", "tone"]
MIN_ALIAS = 3
MAX_STORIES = 12

# Words that leave the company's name when stripped from a listing name.
_SUFFIX = re.compile(
    r"\b(inc|corp|corporation|co|company|ltd|limited|plc|llc|lp|l p|nv|n v|sa|s a|ag|se|"
    r"holdings?|group|trust|class [a-z]|the|com|incorporated|ord|shs|reit|adr|new)\b",
    re.I,
)
_SPACE = re.compile(r"\s+")
_TAG = re.compile(
    r"\((?:NASDAQ|NYSE|NYSEARCA|NYSEAMERICAN|NYSEAMER|AMEX|BATS|CBOE)\s*:\s*\$?([A-Z][A-Z.]{0,5})\)"
)
_CASHTAG = re.compile(r"(?<![\w$])\$([A-Z]{1,5})\b")
# Names that are also an exchange or an index: their tags are everywhere.
_NOT_A_MENTION = frozenset({"nasdaq", "cboe global markets", "intercontinental exchange"})
# Automated sites that publish a story per filing and price move; they would drown the rest.
BLOCKED_DOMAINS = frozenset(
    {
        "themarketsdaily.com",
        "tickerreport.com",
        "dailypolitical.com",
        "thelincolnianonline.com",
        "wkrb13.com",
        "com-unik.info",
        "theenterpriseleader.com",
        "modernreaders.com",
        "americanbankingnews.com",
        "etfdailynews.com",
        "marketbeat.com",
        "zolmax.com",
        "thecerbatgem.com",
        "transcriptdaily.com",
        "baseballnewssource.com",
        "sportsperspectives.com",
        "thestockobserver.com",
        "rivertonroll.com",
        "highlandsnews.com",
        "mayfieldrecorder.com",
        "financial-market-news.com",
        "watchlistnews.com",
        "beaconenterprise.com",
        "dispatchtribunal.com",
        "registrardaily.com",
        "pressoracle.com",
        "slatersentinel.com",
        "tdpelmedia.com",
        "weekherald.com",
        "stocknewstimes.com",
        "chaffeybreeze.com",
        "ledgergazette.com",
        "fairfieldcurrent.com",
        "the-additive.com",
    }
)
_SPAM_TITLE = re.compile(r"^(win|enter to win)\b|\bgiveaway\b|\bcoupon|\bpromo code", re.I)
_BUSINESS = re.compile(
    r"\b(shares?|stocks?|earnings|revenue|sales|profit|loss|quarter|q[1-4]|guidance|forecast|"
    r"outlook|deal|merger|acqui\w*|buyout|takeover|stake|ipo|offering|dividend|buyback|ceo|cfo|"
    r"executive|layoffs?|jobs? cuts?|analysts?|upgrades?|downgrades?|price target|rating|"
    r"lawsuit|sues?|settle\w*|probe|investigation|antitrust|regulators?|fda|approval|recall|"
    r"tariffs?|contract|partnership|investors?|bankruptcy|debt|bonds?|valuation|market cap|"
    r"plunges?|soars?|jumps?|falls?|drops?|rall\w+|slumps?|surges?|tumbles?|sinks?)\b",
    re.I,
)
# "Site Name | Title" or "Title - Site Name": the site's name is not part of the headline.
_SITE_PART = re.compile(r"^[^|]{2,40}\s+\|\s+|\s+[|\-–—]\s+[^|\-–—]{2,40}$")


def company_alias(name: str) -> str:
    """The name a company goes by in headlines: "NVIDIA CORP" -> "nvidia"."""
    text = name.lower().replace(".", " ").replace(",", " ").replace("&", " and ")
    return _SPACE.sub(" ", _SUFFIX.sub(" ", text)).strip()


@dataclass(frozen=True)
class Matcher:
    """Finds the universe companies an article is about."""

    by_alias: dict[str, tuple[str, ...]]
    tickers: frozenset[str]

    @classmethod
    def for_members(cls, members: Sequence[UniverseMember]) -> Matcher:
        groups: dict[str, list[str]] = {}
        for m in members:
            alias = company_alias(m.name)
            if len(alias) >= MIN_ALIAS and alias not in _NOT_A_MENTION:
                groups.setdefault(alias, []).append(m.ticker)
        return cls(
            by_alias={a: tuple(t) for a, t in groups.items()},
            tickers=frozenset(m.ticker for m in members),
        )

    def tickers_for(self, title: str, organisations: Iterable[str]) -> set[str]:
        found = {t for t in _TAG.findall(title) + _CASHTAG.findall(title) if t in self.tickers}
        lowered = title.lower()
        for raw in organisations:
            org = company_alias(raw)  # GDELT writes "apple inc" as well as "apple"
            tickers = self.by_alias.get(org)
            # Whole words, and not an exchange tag ("Nasdaq:" in "(Nasdaq: X)").
            if tickers and re.search(rf"\b{re.escape(org)}\b(?!\s*:)", lowered):
                found.update(tickers)
        return found


def clean_title(raw: str) -> str:
    return _SPACE.sub(" ", html.unescape(raw)).strip()


def is_noise(domain: str, title: str) -> bool:
    return domain.lower() in BLOCKED_DOMAINS or bool(_SPAM_TITLE.search(title)) or len(title) < 15


def story_key(title: str) -> str:
    """Copies of one story (syndicated, or with the site's name added) share this key."""
    core = _SITE_PART.sub("", title.lower())
    return re.sub(r"[^a-z0-9]+", " ", core).strip()


def is_business(title: str) -> bool:
    return bool(_BUSINESS.search(title))


def stories(articles: pd.DataFrame, limit: int = MAX_STORIES) -> pd.DataFrame:
    """One row per story (copies folded together, `copies` counts the outlets), business
    stories first, then the most widely carried, then the earliest."""
    if articles.empty:
        return pd.DataFrame(columns=[*WORLD_COLUMNS, "copies", "business"])
    frame = articles.sort_values("seen_at", kind="mergesort").copy()
    frame["key"] = [story_key(t) for t in frame["title"]]
    copies = frame.groupby("key")["domain"].nunique()
    first = frame.drop_duplicates("key").set_index("key")
    first["copies"] = copies
    first["business"] = [is_business(t) for t in first["title"]]
    ranked = first.sort_values(
        ["business", "copies", "seen_at"], ascending=[False, False, True], kind="mergesort"
    )
    return ranked.head(limit).reset_index(drop=True)


def normalize_world(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in WORLD_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"world news is missing columns: {missing}")
    data = frame[WORLD_COLUMNS].reset_index(drop=True)
    out = pd.DataFrame(
        {
            "ticker": data["ticker"].astype(str),
            "seen_at": pd.to_datetime(data["seen_at"]).dt.as_unit("ns"),
            "title": data["title"].astype(str),
            "url": data["url"].astype(str),
            "domain": data["domain"].astype(str),
            "tone": pd.to_numeric(data["tone"]).astype("float64"),
        }
    )
    return out.drop_duplicates(subset=["ticker", "url"], keep="first").reset_index(drop=True)


def empty_world() -> pd.DataFrame:
    return normalize_world(pd.DataFrame({c: [] for c in WORLD_COLUMNS}))
