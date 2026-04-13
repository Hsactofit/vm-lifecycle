"""Application factory and startup wiring.

Kept intentionally minimal. The lifespan hook is the single place where
application-wide side-effects (logging setup, future DB pool warm-up, etc.)
are initialised, ensuring they run before any request is served and are
cleaned up gracefully on shutdown.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.middleware import RequestTracingMiddleware
from app.api.routes import health, vms
from app.core.config import get_settings
from app.core.exceptions import (
    OpenStackError,
    VMConflictError,
    VMNotFoundError,
    VMOperationError,
    openstack_error_handler,
    vm_conflict_handler,
    vm_not_found_handler,
    vm_operation_handler,
)
from app.core.logging_config import setup_logging

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield
    # Shutdown: close connection pools, flush buffers, etc. (future)


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Production-style REST API for managing VM lifecycle operations. "
        "Simulates OpenStack Nova/Neutron/Cinder integration using a clean "
        "repository pattern that can be swapped for real openstacksdk calls "
        "by setting `BACKEND=openstack`."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── Middleware (outermost first) ──────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestTracingMiddleware)

# ── Exception handlers ────────────────────────────────────────────────────────
app.add_exception_handler(VMNotFoundError, vm_not_found_handler)
app.add_exception_handler(VMConflictError, vm_conflict_handler)
app.add_exception_handler(VMOperationError, vm_operation_handler)
app.add_exception_handler(OpenStackError, openstack_error_handler)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(vms.router, prefix="/api/v1")
