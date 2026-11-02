from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from whymoved.api.health import router as health_router
from whymoved.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Why Did My Stock Move?", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    return app


app = create_app()
