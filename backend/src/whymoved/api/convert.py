"""Turn service results (pandas frames, dataclasses) into API response models."""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
from scipy.stats import t as student_t

from whymoved.api.schemas import (
    CarTestOut,
    FilingOut,
    HeadlineGroupOut,
    HeadlineOut,
    HeadlineReactionOut,
    InsiderFilingOut,
    PermutationOut,
    ReactionOut,
    ReactionPath,
    StockStudyOut,
    WindowOut,
)
from whymoved.domain.insider_summary import SIGNAL_LABELS
from whymoved.domain.reaction import ReactionSize
from whymoved.services.reactions import HeadlineGroup
from whymoved.services.timeline import StockInsiderStudy


def num(value: object) -> float | None:
    """A finite float, or None for NaN and missing values."""
    if value is None:
        return None
    number = float(value)  # type: ignore[arg-type]
    return number if math.isfinite(number) else None


def opt_int(value: object) -> int | None:
    number = num(value)
    return None if number is None else int(number)


def opt_date(value: object) -> date | None:
    if not isinstance(value, date):
        return None
    ts = pd.Timestamp(value)
    return None if pd.isna(ts) else ts.date()


def headlines(frame: pd.DataFrame) -> list[HeadlineOut]:
    return [
        HeadlineOut(
            headline=str(r["headline"]),
            source=str(r["source"]),
            url=str(r["url"]),
            created_at=pd.Timestamp(r["created_at"]).to_pydatetime(),
        )
        for r in frame.to_dict("records")
    ]


def filings(frame: pd.DataFrame) -> list[FilingOut]:
    out: list[FilingOut] = []
    for r in frame.to_dict("records"):
        accepted = r["accepted_at"] if isinstance(r["accepted_at"], date) else None
        accepted_ts = pd.Timestamp(accepted) if accepted is not None else None
        out.append(
            FilingOut(
                accession=str(r["accession"]),
                filing_date=pd.Timestamp(r["filing_date"]).date(),
                accepted_at=(
                    accepted_ts.to_pydatetime()
                    if accepted_ts is not None and not pd.isna(accepted_ts)
                    else None
                ),
                items=[i.strip() for i in str(r["items"]).split(",") if i.strip()],
                labels=[str(label) for label in r["labels"]],
                earnings=bool(r["earnings"]),
                url=str(r["url"]),
                day=pd.Timestamp(r["day"]).date(),
            )
        )
    return out


def insider_filings(frame: pd.DataFrame) -> list[InsiderFilingOut]:
    out: list[InsiderFilingOut] = []
    for r in frame.to_dict("records"):
        out.append(
            InsiderFilingOut(
                accession=str(r["accession"]),
                filing_date=pd.Timestamp(r["filing_date"]).date(),
                trade_date=opt_date(r["trade_date"]),
                day=opt_date(r["day"]),
                signal=r["signal"],
                signal_label=SIGNAL_LABELS[str(r["signal"])],
                owner_name=str(r["owner_name"]),
                owner_role=str(r["owner_role"]),
                plan=str(r["plan"]),
                shares=num(r["shares"]),
                value=num(r["value"]),
                price=num(r["price"]),
                n_transactions=int(r["n_transactions"]),
                lag_days=opt_int(r["lag_days"]),
                url=(
                    f"https://www.sec.gov/Archives/edgar/data/{int(r['issuer_cik'])}/"
                    f"{str(r['accession']).replace('-', '')}/"
                ),
            )
        )
    return out


def stock_study(study: StockInsiderStudy) -> StockStudyOut:
    w = study.windows
    path = study.path
    perm = study.permutation
    critical = float(student_t.ppf(0.975, w.n_used - 1)) if w.n_used >= 2 else float("nan")
    return StockStudyOut(
        n_requested=w.n_requested,
        n_used=w.n_used,
        dropped_incomplete=w.n_dropped_incomplete + w.n_dropped_unmapped,
        dropped_overlap=w.n_dropped_overlap + w.n_dropped_duplicate,
        relative_days=[int(d) for d in w.relative_days],
        mean_car=[float(v) for v in path.mean_car] if path else [],
        ci_low=[num(v) for v in path.ci_low] if path else [],
        ci_high=[num(v) for v in path.ci_high] if path else [],
        tests=[
            CarTestOut(
                start=c.start,
                end=c.end,
                mean_car=c.mean_car,
                se=num(c.se),
                t_stat=num(c.t_stat),
                p_value=num(c.p_value),
                bmp_p=num(c.bmp_p),
                positive_share=c.positive_share,
                ci_low=num(c.mean_car - critical * c.se),
                ci_high=num(c.mean_car + critical * c.se),
            )
            for c in study.tests
        ],
        permutation=(
            PermutationOut(
                observed=perm.observed,
                null_mean=perm.null_mean,
                null_std=perm.null_std,
                p_value=perm.p_value,
                n_permutations=perm.n_permutations,
            )
            if perm
            else None
        ),
    )


def reaction(r: ReactionSize) -> ReactionOut:
    return ReactionOut(
        n_events=r.n_events,
        mean_abs_z_events=r.mean_abs_z_events,
        mean_abs_z_all=r.mean_abs_z_all,
        ratio=r.ratio,
        p_value=r.p_value,
        n_permutations=r.n_permutations,
    )


def headline_groups(groups: list[HeadlineGroup]) -> list[HeadlineGroupOut]:
    out: list[HeadlineGroupOut] = []
    for g in groups:
        r = g.reaction
        reaction = None
        if r is not None:
            reaction = HeadlineReactionOut(
                kind=r.kind,
                session=r.session,
                before=(
                    WindowOut(
                        abnormal=r.before.abnormal, z=num(r.before.z), minutes=r.before.minutes
                    )
                    if r.before
                    else None
                ),
                after=WindowOut(
                    abnormal=r.after.abnormal, z=num(r.after.z), minutes=r.after.minutes
                ),
                p_empirical=r.p_empirical,
                n_reference=r.n_reference,
                flagged=r.flagged,
                moved_before=r.moved_before,
                path=(
                    ReactionPath(
                        minutes=r.path_minutes,
                        actual=r.path_actual,
                        expected=r.path_expected,
                        band=r.path_band,
                    )
                    if r.path_minutes
                    else None
                ),
            )
        out.append(
            HeadlineGroupOut(
                headlines=headlines(g.headlines),
                published=g.published,
                reaction=reaction,
                error=g.error,
            )
        )
    return out
