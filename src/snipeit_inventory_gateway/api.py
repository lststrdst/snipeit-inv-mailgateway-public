from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import load_config
from .errors import AuthenticationError, ValidationError
from .protocol import decode_event
from .queue import EventQueue

LOG = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    app.state.config = config
    app.state.queue = EventQueue(config.queue.path)
    yield
    app.state.queue.close()


app = FastAPI(
    title="SnipeIT Inventory Gateway",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def narrow_surface(request: Request, call_next):
    if request.url.path != "/api/v1/events" or request.method != "POST":
        return JSONResponse({"status": "not_found"}, status_code=404)
    request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    length = request.headers.get("content-length")
    if length and (
        not length.isdigit() or int(length) > request.app.state.config.api.max_body_bytes
    ):
        return JSONResponse(
            {
                "status": "rejected",
                "error": "body_too_large",
                "gateway_version": __version__,
                "request_id": request_id,
            },
            status_code=413,
        )
    return await call_next(request)


@app.post("/api/v1/events")
async def ingest(request: Request):
    request_id = request.state.request_id
    raw = await request.body()
    if len(raw) > request.app.state.config.api.max_body_bytes:
        return JSONResponse(
            {
                "status": "rejected",
                "error": "body_too_large",
                "gateway_version": __version__,
                "request_id": request_id,
            },
            status_code=413,
        )
    try:
        event = decode_event(raw, request.app.state.config, enforce_freshness=True)
        status, duplicate = request.app.state.queue.enqueue(event, "https")
    except AuthenticationError:
        LOG.warning("authentication_failed request_id=%s", request_id)
        return JSONResponse(
            {
                "status": "rejected",
                "error": "authentication_failed",
                "gateway_version": __version__,
                "request_id": request_id,
            },
            status_code=401,
        )
    except (ValidationError, json.JSONDecodeError):
        LOG.info("validation_failed request_id=%s", request_id)
        return JSONResponse(
            {
                "status": "rejected",
                "error": "invalid_event",
                "gateway_version": __version__,
                "request_id": request_id,
            },
            status_code=400,
        )
    return JSONResponse(
        {
            "status": "duplicate" if duplicate else "accepted",
            "queue_status": status,
            "event_id": event.event_id,
            "gateway_version": __version__,
            "request_id": request_id,
        },
        status_code=200 if duplicate else 202,
    )
