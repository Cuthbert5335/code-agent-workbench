"""Minimal request logging that avoids bodies, files and credentials."""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from fastapi import Request, Response

logger = logging.getLogger("uvicorn.error")


async def log_request(request: Request, call_next) -> Response:
    """Log safe request metadata and expose a request ID to the client."""

    request_id = request.headers.get("X-Request-ID") or uuid4().hex
    request.state.request_id = request_id
    started_at = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.exception(
            "request_failed request_id=%s method=%s path=%s elapsed_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            elapsed_ms,
        )
        raise

    elapsed_ms = (time.perf_counter() - started_at) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_completed request_id=%s method=%s path=%s status=%s elapsed_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response
