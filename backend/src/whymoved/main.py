import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from whymoved.adapters.alpaca_intraday import build_intraday
from whymoved.api.errors import install_error_handlers
from whymoved.api.health import router as health_router
from whymoved.api.peers import router as peers_router
from whymoved.api.state import DataState
from whymoved.api.v1 import router as v1_router
from whymoved.config import Settings, get_settings
from whymoved.services.explain import MoveExplainer
from whymoved.services.reactions import ReactionService

logger = logging.getLogger(__name__)


async def _keep_fresh(state: DataState, settings: Settings) -> None:
    """Load the snapshot now, then reload it every `snapshot_refresh_hours`."""
    while True:
        await asyncio.to_thread(state.refresh, settings.data_dir, settings.snapshot_url)
        if not settings.snapshot_url or settings.snapshot_refresh_hours <= 0:
            return
        await asyncio.sleep(settings.snapshot_refresh_hours * 3600.0)


def _reactions(settings: Settings) -> ReactionService:
    if settings.alpaca_key_id is None or settings.alpaca_secret_key is None:
        logger.info("no Alpaca keys: intraday headline reactions are disabled")
        return ReactionService(None)
    return ReactionService(
        build_intraday(
            settings.alpaca_key_id, settings.alpaca_secret_key, feed=settings.alpaca_feed
        )
    )


def create_app(
    explainer: MoveExplainer | None = None,
    settings: Settings | None = None,
    reactions: ReactionService | None = None,
) -> FastAPI:
    """Build the API. Tests pass a ready explainer; otherwise the snapshot loads in the
    background, so the server answers /health immediately while the data is loading."""
    settings = settings or get_settings()
    state = DataState(explainer)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = None if explainer is not None else asyncio.create_task(_keep_fresh(state, settings))
        yield
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Why Did My Stock Move?", version="0.2.0", lifespan=lifespan)
    app.state.data = state
    app.state.reactions = reactions or _reactions(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(v1_router)
    app.include_router(peers_router)
    return app


app = create_app()
