"""Health endpoints.

Two probes — matching Kubernetes / load-balancer conventions:

  GET /health        → liveness probe
    Returns 200 as long as the process is running.
    Kubernetes restarts the container if this fails.
    Should never depend on external systems.

  GET /health/ready  → readiness probe
    Returns 200 only when the service can handle traffic.
    Validates backend connectivity for BACKEND=openstack.
    Kubernetes stops routing traffic (without restarting) if this fails.
    Use this as the target health check in load balancers.
"""
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import Settings, get_settings

router = APIRouter(tags=["Operations"])
logger = logging.getLogger(__name__)


class LivenessResponse(BaseModel):
    status: str
    version: str
    backend: str


class ReadinessResponse(BaseModel):
    status: str
    backend: str
    detail: str


@router.get(
    "/health",
    response_model=LivenessResponse,
    summary="Liveness probe",
    description="Returns 200 if the process is running. Does not check backend connectivity.",
)
async def liveness(settings: Settings = Depends(get_settings)) -> LivenessResponse:
    return LivenessResponse(
        status="ok",
        version=settings.APP_VERSION,
        backend=settings.BACKEND,
    )


@router.get(
    "/health/ready",
    summary="Readiness probe",
    description=(
        "Returns 200 when the service can serve traffic. "
        "For BACKEND=openstack, validates Nova connectivity. "
        "Returns 503 if the backend is unreachable."
    ),
    responses={503: {"description": "Backend not ready"}},
)
async def readiness(settings: Settings = Depends(get_settings)) -> JSONResponse:
    if settings.BACKEND == "openstack":
        try:
            from app.api.deps import _openstack_singleton
            repo = _openstack_singleton()
            await __import__("asyncio").to_thread(repo.ping)
            detail = "nova reachable"
        except Exception as exc:
            logger.warning("readiness_check_failed", extra={"backend": "openstack", "error": str(exc)})
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "backend": settings.BACKEND,
                    "detail": f"OpenStack backend unavailable: {exc}",
                },
            )
    elif settings.BACKEND == "sqlite":
        # SQLite is always ready if the file path is writable (checked at init).
        detail = "sqlite ready"
    else:
        detail = "mock ready"

    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "backend": settings.BACKEND,
            "detail": detail,
        },
    )
