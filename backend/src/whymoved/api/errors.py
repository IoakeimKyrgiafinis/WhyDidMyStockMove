from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from whymoved.api.state import DataUnavailable
from whymoved.services.explain import NotEnoughData, UnknownTicker

logger = logging.getLogger(__name__)


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


async def _unknown_ticker(_: Request, exc: Exception) -> JSONResponse:
    ticker = exc.args[0] if exc.args else ""
    return error_response(404, "unknown_ticker", f"{ticker} is not in the covered universe")


async def _not_enough_data(_: Request, exc: Exception) -> JSONResponse:
    return error_response(422, "not_enough_data", str(exc))


async def _unavailable(_: Request, exc: Exception) -> JSONResponse:
    return error_response(503, "data_unavailable", str(exc))


async def _invalid(_: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "path")
        message = f"{where}: {first.get('msg', 'invalid value')}" if where else "invalid request"
    else:
        message = str(exc)
    return error_response(422, "invalid_request", message)


async def _http(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    code = "not_found" if exc.status_code == 404 else "http_error"
    return error_response(exc.status_code, code, str(exc.detail))


async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error", exc_info=exc)
    return error_response(500, "internal_error", "something went wrong on our side")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(UnknownTicker, _unknown_ticker)
    app.add_exception_handler(NotEnoughData, _not_enough_data)
    app.add_exception_handler(DataUnavailable, _unavailable)
    app.add_exception_handler(RequestValidationError, _invalid)
    app.add_exception_handler(ValueError, _invalid)
    app.add_exception_handler(HTTPException, _http)
    app.add_exception_handler(Exception, _unexpected)
