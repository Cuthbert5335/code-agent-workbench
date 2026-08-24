"""HTTP entry point for the CodeXXX backend."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analysis import router as analysis_router
from app.api.auth import router as auth_router
from app.api.chunks import router as chunks_router
from app.api.indexing import router as indexing_router
from app.api.organizations import router as organizations_router
from app.api.patches import router as patches_router
from app.api.projects import router as projects_router
from app.api.retention import router as retention_router
from app.api.sandbox import router as sandbox_router
from app.api.search import router as search_router
from app.api.symbols import router as symbols_router
from app.api.tasks import router as tasks_router
from app.api.usage import router as usage_router
from app.config import settings
from app.observability import log_request
from app.services.agent_tasks import agent_task_service
from app.services.retention import retention_service


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run the durable queue dispatcher for the application lifetime."""

    agent_task_service.start_queue(settings)
    retention_service.start(settings)
    try:
        yield
    finally:
        retention_service.stop(wait=True)
        agent_task_service.stop_queue(wait=True)

app = FastAPI(
    title="CodeXXX API",
    version="0.1.0",
    description="Local API for the CodeXXX code assistant.",
    lifespan=lifespan,
)

# The frontend will run on Vite's development port in a later milestone.
# Keeping CORS explicit makes the local browser-to-API connection predictable.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.middleware("http")(log_request)
app.include_router(analysis_router)
app.include_router(auth_router)
app.include_router(chunks_router)
app.include_router(indexing_router)
app.include_router(organizations_router)
app.include_router(patches_router)
app.include_router(projects_router)
app.include_router(retention_router)
app.include_router(sandbox_router)
app.include_router(search_router)
app.include_router(symbols_router)
app.include_router(tasks_router)
app.include_router(usage_router)


@app.get("/api/health", tags=["system"])
async def health() -> dict[str, str]:
    """Return a small, dependency-light readiness response."""

    return {
        "status": "ok",
        "service": "CodeXXX API",
        "version": "0.1.0",
    }
