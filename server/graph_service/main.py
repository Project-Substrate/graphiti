# Copyright (c) 2024-2026 Magnon Compute Corporation. All Rights Reserved.

import asyncio
import os
import socket
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from graph_service.config import get_settings
from graph_service.routers import ingest, retrieve
from graph_service.zep_graphiti import initialize_graphiti


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    await initialize_graphiti(settings)
    yield
    # Shutdown
    # No need to close Graphiti here, as it's handled per-request


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Propagate or generate X-Request-ID; set request.state.request_id."""

    async def dispatch(self, request: Request, call_next):
        request_id = (
            request.headers.get("X-Request-ID")
            or request.headers.get("X-Trace-ID")
            or str(uuid.uuid4())
        )
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


_OPENAPI_TAGS = [
    {"name": "health", "description": "Liveness, readiness, and dependency health checks"},
    {"name": "meta", "description": "Service version and build metadata"},
    {"name": "ingest", "description": "Graph data ingestion"},
    {"name": "retrieve", "description": "Graph data retrieval"},
]

app = FastAPI(
    title="Graphiti Graph Service",
    description="Temporally-aware knowledge graph service",
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=_OPENAPI_TAGS,
)

app.add_middleware(RequestIDMiddleware)

app.include_router(retrieve.router)
app.include_router(ingest.router)


async def _check_tcp(host: str, port: int, timeout: float = 2.0) -> str:
    """Check TCP connectivity to host:port."""
    try:
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(
            loop.run_in_executor(None, lambda: socket.create_connection((host, port), timeout)),
            timeout=timeout,
        )
        return "ok"
    except Exception as exc:
        return f"unreachable: {exc}"


@app.get('/health', tags=["health"])
async def health():
    return JSONResponse(content={'status': 'ok', 'service': 'graphiti-graph-service'}, status_code=200)


@app.get('/health/dependencies', tags=["health"])
async def health_dependencies():
    """Probe all external services this service connects to."""
    settings = get_settings()
    # neo4j_uri is e.g. bolt://host:7687 or neo4j://host:7687
    neo4j_uri = settings.neo4j_uri
    neo4j_host = "localhost"
    neo4j_port = 7687
    try:
        # parse host:port from uri
        parts = neo4j_uri.split("://", 1)[-1].split(":")
        neo4j_host = parts[0]
        neo4j_port = int(parts[1]) if len(parts) > 1 else 7687
    except Exception:
        pass

    neo4j_status = await _check_tcp(neo4j_host, neo4j_port)

    deps = {
        "neo4j": {"host": neo4j_host, "port": neo4j_port, "status": neo4j_status},
    }
    overall = "ok" if all(v["status"] == "ok" for v in deps.values()) else "degraded"
    return {"status": overall, "service": "graphiti-graph-service", "dependencies": deps}


@app.get('/version', tags=["meta"])
async def version():
    """Service version information."""
    return {
        "service": "graphiti-graph-service",
        "version": os.getenv("SERVICE_VERSION", "1.0.0"),
        "build_hash": os.getenv("BUILD_HASH", "unknown"),
    }


@app.get('/healthcheck', tags=["health"], include_in_schema=False)
async def healthcheck():
    """Legacy alias for /health."""
    return JSONResponse(content={'status': 'ok', 'service': 'graphiti-graph-service'}, status_code=200)
