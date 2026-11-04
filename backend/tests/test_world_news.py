from __future__ import annotations

import io
import json
import math
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Self

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import whymoved.jobs.collect_world_news as job
from market import Market, make_market, write_snapshot
from whymoved.adapters.gdelt import GdeltFiles, GkgArticle, file_url, parse_gkg, stamps
from whymoved.adapters.http_policy import RetryPolicy
from whymoved.adapters.json_http import SourceError, SourceUnavailable
from whymoved.adapters.snapshot import load_snapshot
from whymoved.adapters.world_store import (
    ParquetWorldStore,
    coverage_start,
    read_seen_times,
    read_ticker,
)
from whymoved.domain.universe import UniverseMember
from whymoved.domain.world_news import (
    WORLD_COLUMNS,
    Matcher,
    company_alias,
    empty_world,
    is_noise,
    normalize_world,
    stories,
    story_key,
)
from whymoved.main import create_app
from whymoved.services.explain import MoveExplainer
from whymoved.services.world_news import collect_world_news, merge_world

T = pd.Timestamp


def member(ticker: str, name: str) -> UniverseMember:
    return UniverseMember(
        ticker=ticker, name=name, sector="Information Technology", sector_etf="XLK", weight=1.0
    )


MATCHER = Matcher.for_members(
    [
        member("NVDA", "NVIDIA CORP"),
        member("AAPL", "APPLE INC"),
        member("NDAQ", "NASDAQ INC"),
        member("GOOGL", "ALPHABET INC CLASS A"),
        member("GOOG", "ALPHABET INC CLASS C"),
        member("X", "XO"),  # too short to match by name
    ]
)


# ---- matching ----


def test_company_aliases() -> None:
    assert company_alias("NVIDIA CORP") == "nvidia"
    assert company_alias("ALPHABET INC CLASS A") == "alphabet"
    assert company_alias("Johnson & Johnson") == "johnson and johnson"
    assert company_alias("S&P Global Inc.") == "s and p global"


def test_an_article_needs_the_name_in_its_title_and_organisations() -> None:
    assert MATCHER.tickers_for("Nvidia shares jump", ["nvidia"]) == {"NVDA"}
    assert MATCHER.tickers_for("Nvidia shares jump", ["apple inc"]) == set()
    assert MATCHER.tickers_for("Chip stocks rally", ["nvidia"]) == set()
    # GDELT's long form of the name is reduced the same way.
    assert MATCHER.tickers_for("Apple results beat", ["apple inc"]) == {"AAPL"}
    # One name, two share classes.
    assert MATCHER.tickers_for("Alphabet earnings", ["alphabet"]) == {"GOOG", "GOOGL"}


def test_exchange_tags_and_cashtags_match_without_organisations() -> None:
    assert MATCHER.tickers_for("Big news for (NASDAQ:NVDA) holders", []) == {"NVDA"}
    assert MATCHER.tickers_for("Why $AAPL fell", []) == {"AAPL"}
    assert MATCHER.tickers_for("Why $ZZZZ fell", []) == set()


def test_exchange_names_are_not_mentions_of_the_exchange_company() -> None:
    assert MATCHER.tickers_for("Shopify (NASDAQ:SHOP) shares gap down", ["nasdaq"]) == set()


def test_noise() -> None:
    assert is_noise("tickerreport.com", "Nvidia shares sold by a fund manager")
    assert is_noise("example.com", "Win 1 of 5 Apple watches in our draw")
    assert is_noise("example.com", "Apple")
    assert not is_noise("reuters.com", "Apple results beat estimates")


def test_copies_of_a_story_share_a_key() -> None:
    a = story_key("Nvidia lifts Wall Street - The Gazette")
    b = story_key("Daily News | Nvidia lifts Wall Street")
    c = story_key("Nvidia lifts Wall Street!")
    assert a == b == c == "nvidia lifts wall street"


def test_stories_fold_copies_and_rank_business_first() -> None:
    articles = normalize_world(
        pd.DataFrame(
            [
                ["NVDA", "2026-11-02 12:00", "Nvidia is in a new film", "u1", "a.com", 0.0],
                ["NVDA", "2026-11-02 12:05", "Nvidia is in a new film", "u2", "b.com", 0.0],
                ["NVDA", "2026-11-02 12:06", "Nvidia is in a new film", "u3", "c.com", 0.0],
                ["NVDA", "2026-11-02 13:00", "Nvidia shares jump", "u4", "d.com", 1.0],
                ["NVDA", "2026-11-02 14:00", "Nvidia earnings beat", "u5", "e.com", 1.0],
                ["NVDA", "2026-11-02 14:30", "Nvidia earnings beat", "u6", "f.com", 1.0],
            ],
            columns=WORLD_COLUMNS,
        )
    )
    out = stories(articles)
    assert out["title"].tolist() == [
        "Nvidia earnings beat",
        "Nvidia shares jump",
        "Nvidia is in a new film",
    ]
    assert out["copies"].tolist() == [2, 1, 3]
    assert out["url"].iloc[0] == "u5"  # the first copy seen
    assert stories(articles, limit=1)["title"].tolist() == ["Nvidia earnings beat"]
    assert stories(empty_world()).empty


def test_normalize_needs_every_column_and_drops_repeated_urls() -> None:
    row = ["NVDA", "2026-11-02 12:00", "Nvidia shares jump", "u1", "a.com", 1.0]
    frame = pd.DataFrame([row, row], columns=WORLD_COLUMNS)
    assert len(normalize_world(frame)) == 1
    with pytest.raises(ValueError, match="missing columns"):
        normalize_world(frame.drop(columns=["tone"]))


# ---- GDELT files ----


def gkg_zip(rows: list[list[str]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("x.gkg.csv", "\n".join("\t".join(r) for r in rows) + "\n")
    return buffer.getvalue()


def gkg_row(seen: str, title: str, orgs: str, tone: str = "1.5,2,0") -> list[str]:
    cells = [""] * 27
    cells[1] = seen
    cells[3] = "Example.com"
    cells[4] = f"https://example.com/{seen}"
    cells[13] = orgs
    cells[15] = tone
    cells[26] = (
        f"<PAGE_PRECISEPUBTIMESTAMP>{seen}</PAGE_PRECISEPUBTIMESTAMP><PAGE_TITLE>{title}</PAGE_TITLE>"
    )
    return cells


def test_parse_gkg_reads_title_organisations_and_tone() -> None:
    content = gkg_zip(
        [
            gkg_row("20261102120000", "Nvidia shares jump", "nvidia;tsmc"),
            gkg_row("20261102120000", "No tone here", "", tone="x"),
            gkg_row("not a date", "Bad", ""),
            ["too", "short"],
        ]
    )
    rows = list(parse_gkg(content))
    assert len(rows) == 2
    first = rows[0]
    assert first.seen_at == datetime(2026, 11, 2, 12, 0)
    assert (first.domain, first.title, first.organisations) == (
        "example.com",
        "Nvidia shares jump",
        ("nvidia", "tsmc"),
    )
    assert first.tone == 1.5
    assert math.isnan(rows[1].tone)
    with pytest.raises(SourceError):
        list(parse_gkg(b"not a zip"))
    assert list(parse_gkg(gkg_zip([]))) == []


def test_file_times_follow_the_15_minute_grid() -> None:
    out = stamps(datetime(2026, 11, 2, 12, 7), datetime(2026, 11, 2, 12, 45))
    assert out == [
        datetime(2026, 11, 2, 12, 15),
        datetime(2026, 11, 2, 12, 30),
        datetime(2026, 11, 2, 12, 45),
    ]
    assert file_url(out[0]).endswith("/gdeltv2/20261102121500.gkg.csv.zip")


def files(handler: object) -> GdeltFiles:
    return GdeltFiles(
        httpx.Client(transport=httpx.MockTransport(handler)),  # type: ignore[arg-type]
        retry=RetryPolicy(max_attempts=2, base_delay=0.0, max_delay=0.0),
        sleep=lambda _s: None,
    )


def test_downloads_missing_files_and_retries() -> None:
    content = gkg_zip([gkg_row("20261102120000", "Nvidia shares jump", "nvidia")])
    calls: list[int] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, content=content)

    stamp = datetime(2026, 11, 2, 12, 0)
    assert len(files(flaky).articles(stamp) or []) == 1
    assert files(lambda r: httpx.Response(404)).articles(stamp) is None
    with pytest.raises(SourceUnavailable):
        files(lambda r: httpx.Response(500)).articles(stamp)
    with pytest.raises(SourceError):
        files(lambda r: httpx.Response(403)).articles(stamp)

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(SourceUnavailable):
        files(broken).articles(stamp)


# ---- collecting ----


class FakeGdelt:
    def __init__(self, fail_at: datetime | None = None) -> None:
        self.fail_at = fail_at
        self.asked: list[datetime] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def articles(self, stamp: datetime) -> list[GkgArticle] | None:
        if stamp == self.fail_at:
            raise SourceUnavailable("down")
        self.asked.append(stamp)
        if stamp.minute == 30:
            return None  # GDELT skipped this file
        return [
            GkgArticle(
                stamp,
                "reuters.com",
                f"https://r/{stamp:%H%M}",
                "Nvidia shares jump",
                ("nvidia",),
                1.0,
            ),
            GkgArticle(
                stamp,
                "tickerreport.com",
                f"https://t/{stamp:%H%M}",
                "Nvidia shares sold",
                ("nvidia",),
                0.0,
            ),
            GkgArticle(stamp, "a.com", f"https://a/{stamp:%H%M}", "Weather today", (), 0.0),
            GkgArticle(stamp, "a.com", f"https://b/{stamp:%H%M}", "", ("nvidia",), 0.0),
        ]


def test_collect_keeps_matched_articles_and_skips_missing_files() -> None:
    times = stamps(datetime(2026, 11, 2, 12, 0), datetime(2026, 11, 2, 13, 0))
    frame, report = collect_world_news(FakeGdelt(), MATCHER, times)
    assert report.n_files == 4 and report.n_missing == 1
    assert report.through == datetime(2026, 11, 2, 13, 0)
    assert report.error is None
    assert frame["domain"].unique().tolist() == ["reuters.com"]
    assert len(frame) == 3


def test_collect_stops_at_a_failing_file_and_at_the_deadline() -> None:
    times = stamps(datetime(2026, 11, 2, 12, 0), datetime(2026, 11, 2, 13, 0))
    frame, report = collect_world_news(FakeGdelt(fail_at=times[2]), MATCHER, times)
    assert report.through == times[1] and report.error is not None and len(frame) == 1

    ticks = iter([0.0, 0.0, 100.0])
    _, report = collect_world_news(
        FakeGdelt(), MATCHER, times, deadline=50.0, clock=lambda: next(ticks)
    )
    assert report.n_files == 2


def test_merge_drops_old_articles() -> None:
    old = normalize_world(
        pd.DataFrame(
            [["NVDA", "2025-01-01", "Nvidia old news", "u0", "a.com", 0.0]], columns=WORLD_COLUMNS
        )
    )
    new = normalize_world(
        pd.DataFrame(
            [["NVDA", "2026-11-02", "Nvidia new news", "u1", "a.com", 0.0]], columns=WORLD_COLUMNS
        )
    )
    assert merge_world(old, new, keep_days=30)["url"].tolist() == ["u1"]
    assert merge_world(empty_world(), empty_world(), keep_days=30).empty


def test_store_reads_one_stock_and_one_window(tmp_path: Path) -> None:
    store = ParquetWorldStore(tmp_path)
    assert store.load().empty and store.read_through() is None
    assert read_ticker(store.path, "NVDA").empty
    assert read_seen_times(store.path, "NVDA").empty
    assert coverage_start(store.path) is None
    rows = [
        ["NVDA", "2026-11-01 10:00", "Nvidia a", "u1", "a.com", 0.0],
        ["NVDA", "2026-11-02 10:00", "Nvidia b", "u2", "a.com", 0.0],
        ["AAPL", "2026-10-31 10:00", "Apple c", "u3", "a.com", 0.0],
    ]
    store.save(pd.DataFrame(rows, columns=WORLD_COLUMNS))
    store.write_through(datetime(2026, 11, 2, 12, 0))
    assert store.read_through() == datetime(2026, 11, 2, 12, 0)
    assert len(store.load()) == 3
    window = read_ticker(store.path, "NVDA", datetime(2026, 11, 2), datetime(2026, 11, 3))
    assert window["url"].tolist() == ["u2"]
    assert len(read_seen_times(store.path, "NVDA")) == 2
    assert coverage_start(store.path) == T("2026-10-31 10:00")
    (tmp_path / "world_news_state.json").write_text("{}", encoding="utf-8")
    with pytest.raises(Exception, match="unreadable"):
        store.read_through()


def universe_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    summary = {
        "as_of": "2026-11-02",
        "history_start": "2020-01-02",
        "members": [
            {
                "ticker": "NVDA",
                "name": "NVIDIA CORP",
                "sector": "IT",
                "sector_etf": "XLK",
                "weight": 1.0,
            }
        ],
    }
    (data / "universe.json").write_text(json.dumps(summary), encoding="utf-8")
    return data


def test_job_reads_from_where_it_stopped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = universe_dir(tmp_path)
    fake = FakeGdelt()
    monkeypatch.setattr(job, "gdelt_files", lambda: fake)
    now = datetime(2026, 11, 2, 13, 30)
    assert job.main(["--data-dir", str(data), "--backfill-days", "0.05"], now=now) == 0
    first = list(fake.asked)
    assert first[-1] == datetime(2026, 11, 2, 13, 0)
    assert job.main(["--data-dir", str(data)], now=datetime(2026, 11, 2, 14, 0)) == 0
    assert fake.asked[len(first) :] == [
        datetime(2026, 11, 2, 13, 15),
        datetime(2026, 11, 2, 13, 30),
    ]
    assert len(ParquetWorldStore(data).load()) > 0


def test_job_reports_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert job.main(["--data-dir", str(tmp_path)]) == 2  # no universe yet
    data = universe_dir(tmp_path)
    now = datetime(2026, 11, 2, 13, 30)
    monkeypatch.setattr(job, "gdelt_files", lambda: FakeGdelt(fail_at=datetime(2026, 11, 2, 13, 0)))
    assert job.main(["--data-dir", str(data), "--backfill-days", "0.05"], now=now) == 1
    assert ParquetWorldStore(data).read_through() == datetime(2026, 11, 2, 12, 45)
    (data / "world_news.parquet").write_bytes(b"broken")
    assert job.main(["--data-dir", str(data)], now=now) == 1


# ---- serving ----


def test_the_day_view_shows_coverage_against_a_usual_day(tmp_path: Path) -> None:
    market: Market = make_market()
    write_snapshot(tmp_path, market)
    days = market.close.index
    rows: list[list[object]] = []
    # One article a day for 30 trading days (at 14:00 UTC, inside the session), then a busy day.
    for k, day in enumerate(days[-31:-1]):
        rows.append(
            ["AAA", day + pd.Timedelta(hours=14), f"Alpha business deal {k}", f"u{k}", "a.com", 0.0]
        )
    busy = days[-1]
    for k in range(5):
        rows.append(
            ["AAA", busy + pd.Timedelta(hours=14), "Alpha shares jump", f"b{k}", f"s{k}.com", 1.0]
        )
    ParquetWorldStore(tmp_path).save(pd.DataFrame(rows, columns=WORLD_COLUMNS))

    client = TestClient(create_app(MoveExplainer(load_snapshot(tmp_path), n_boot=50)))
    world = client.get(f"/api/v1/stocks/AAA/days/{busy.date().isoformat()}").json()["items"][
        "world"
    ]
    assert world["covered"] is True
    assert world["n_articles"] == 5
    assert world["typical"] == 1.0
    assert world["stories"][0]["copies"] == 5

    early = client.get(f"/api/v1/stocks/AAA/days/{days[-60].date().isoformat()}").json()
    assert early["items"]["world"]["covered"] is False
    assert early["items"]["world"]["since"] == days[-31].date().isoformat()


def test_without_world_news_the_day_view_says_so() -> None:
    client = TestClient(create_app(MoveExplainer(make_market().snapshot, n_boot=50)))
    day = make_market().close.index[-1].date().isoformat()
    world = client.get(f"/api/v1/stocks/AAA/days/{day}").json()["items"]["world"]
    assert world == {
        "covered": False,
        "since": None,
        "n_articles": 0,
        "typical": None,
        "stories": [],
    }
