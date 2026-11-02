"""Peer grid endpoints. The grid and the replay also come as Server-Sent Events streams, so
the page can draw while the server is still computing (the cointegration tests and the
replay frames are produced one at a time)."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from datetime import date
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from whymoved.api.convert import num
from whymoved.api.v1 import ERRORS, get_explainer
from whymoved.services.explain import MoveExplainer
from whymoved.services.peers import FDR_Q, PeerResult

router = APIRouter(prefix="/api/v1", tags=["peers"], responses=ERRORS)
Explainer = Annotated[MoveExplainer, Depends(get_explainer)]


class PeerRow(BaseModel):
    ticker: str
    name: str
    sector: str
    corr_selection: float
    ci_low_selection: float
    ci_high_selection: float
    corr_validation: float | None
    ci_low_validation: float | None
    ci_high_validation: float | None
    spillover_beta: float | None
    spillover_se: float | None


class CointegrationRow(BaseModel):
    ticker: str
    statistic: float
    p_value: float
    q_value: float | None
    cointegrated: bool | None
    hedge_ratio: float


class PeersResponse(BaseModel):
    target: str
    selection_start: date
    split: date
    end: date
    n_universe: int
    peers: list[PeerRow]
    order: list[str]
    grids: dict[str, list[list[float | None]]]
    cointegration: list[CointegrationRow]
    fdr_q: float


class ComoveRow(BaseModel):
    ticker: str
    actual: float
    expected: float
    z: float | None
    beta: float


class ComoveResponse(BaseModel):
    target: str
    date: date
    own_unexplained: float
    peers: list[ComoveRow]


def _grid(values: object) -> list[list[float | None]]:
    return [[None if not math.isfinite(v) else round(float(v), 3) for v in row] for row in values]  # type: ignore[attr-defined]


def _peer_rows(explainer: MoveExplainer, result: PeerResult) -> list[PeerRow]:
    rows: list[PeerRow] = []
    for ticker, r in result.ranking.iterrows():
        t = str(ticker)
        member = explainer.member(t)
        v = result.validation.loc[t]
        s = result.spillover.loc[t]
        rows.append(
            PeerRow(
                ticker=t,
                name=member.name,
                sector=member.sector,
                corr_selection=float(r["corr"]),
                ci_low_selection=float(r["ci_low"]),
                ci_high_selection=float(r["ci_high"]),
                corr_validation=num(v["corr_validation"]),
                ci_low_validation=num(v["val_ci_low"]),
                ci_high_validation=num(v["val_ci_high"]),
                spillover_beta=num(s["beta"]),
                spillover_se=num(s["se"]),
            )
        )
    return rows


def _summary(explainer: MoveExplainer, result: PeerResult) -> dict[str, object]:
    w = result.window
    return {
        "target": result.target,
        "selection_start": w.start.isoformat(),
        "split": w.split.isoformat(),
        "end": w.end.isoformat(),
        "n_universe": len(w.selection.columns),
        "peers": [row.model_dump() for row in _peer_rows(explainer, result)],
        "order": result.order,
        "grids": {name: _grid(m) for name, m in result.grids.items()},
        "fdr_q": FDR_Q,
    }


@router.get("/stocks/{ticker}/peers", response_model=PeersResponse)
def peers(ticker: str, explainer: Explainer) -> PeersResponse:
    """The top 99 peers by residual correlation, the 100x100 grid and the cointegration tests."""
    result = explainer.peers.peers(ticker)
    table = explainer.peers.cointegration_table(result.target)
    return PeersResponse.model_validate(
        {
            **_summary(explainer, result),
            "cointegration": [
                CointegrationRow(ticker=str(t), **{k: r[k] for k in table.columns})
                for t, r in table.iterrows()
            ],
        }
    )


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


@router.get("/stocks/{ticker}/peers/stream")
def peers_stream(ticker: str, explainer: Explainer) -> StreamingResponse:
    """Server-Sent Events: `peers` (ranking and grid), then one `cointegration` event per
    tested peer as each test finishes, then `fdr` with the corrected results, then `done`."""
    result = explainer.peers.peers(ticker)  # errors surface here, before streaming starts

    def events() -> Iterator[str]:
        yield _sse("peers", _summary(explainer, result))
        for peer, r in explainer.peers.cointegration(result.target):
            payload = (
                {
                    "ticker": peer,
                    "statistic": r.statistic,
                    "p_value": r.p_value,
                    "hedge_ratio": r.hedge_ratio,
                }
                if r is not None
                else {"ticker": peer, "error": "the test could not run"}
            )
            yield _sse("cointegration", payload)
        table = explainer.peers.cointegration_table(result.target)
        yield _sse(
            "fdr",
            [
                {
                    "ticker": str(t),
                    "q_value": float(r["q_value"]),
                    "cointegrated": bool(r["cointegrated"]),
                }
                for t, r in table.iterrows()
            ],
        )
        yield _sse("done", {})

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.get("/stocks/{ticker}/peers/comove", response_model=ComoveResponse)
def comove(ticker: str, day: Annotated[date, Query()], explainer: Explainer) -> ComoveResponse:
    """On one day: how each top peer's unexplained move compares with what its spillover beta
    predicted from this stock's unexplained move."""
    member = explainer.member(ticker)
    table = explainer.peers.comove(member.ticker, day)
    own = explainer.daily(member.ticker)
    return ComoveResponse(
        target=member.ticker,
        date=day,
        own_unexplained=float(own.loc[pd.Timestamp(day), "unexplained"]),  # type: ignore[arg-type]
        peers=[
            ComoveRow(
                ticker=str(t),
                actual=float(r["actual"]),
                expected=float(r["expected"]),
                z=num(r["z"]),
                beta=float(r["beta"]),
            )
            for t, r in table.iterrows()
        ],
    )


@router.get("/stocks/{ticker}/peers/replay")
def replay(ticker: str, explainer: Explainer) -> StreamingResponse:
    """Server-Sent Events: `names`, then one `frame` per step of a rolling three-month
    correlation window across the whole history, computed as they are sent."""
    frames = explainer.peers.replay(ticker)
    first = next(frames)  # errors surface here, before streaming starts

    def events() -> Iterator[str]:
        yield _sse("names", first)
        for frame in frames:
            yield _sse("frame", frame)
        yield _sse("done", {})

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )
