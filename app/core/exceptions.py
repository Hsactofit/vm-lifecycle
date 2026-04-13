"""Domain exceptions and FastAPI exception handlers.

All domain errors are subclasses of a base VMError so callers can catch
broadly or narrowly. FastAPI handlers convert them to structured JSON responses
with consistent error codes — making the API contract explicit and testable.
"""
from fastapi import Request
from fastapi.responses import JSONResponse


# ── Domain exceptions ─────────────────────────────────────────────────────────

class VMError(Exception):
    """Base class for all VM lifecycle errors."""


class VMNotFoundError(VMError):
    def __init__(self, vm_id: str) -> None:
        self.vm_id = vm_id
        super().__init__(f"VM '{vm_id}' not found")


class VMConflictError(VMError):
    def __init__(self, vm_id: str, message: str) -> None:
        self.vm_id = vm_id
        super().__init__(message)


class VMOperationError(VMError):
    """Raised when an operation is invalid given the VM's current state."""

    def __init__(self, vm_id: str, operation: str, reason: str) -> None:
        self.vm_id = vm_id
        self.operation = operation
        super().__init__(f"Cannot '{operation}' VM '{vm_id}': {reason}")


class OpenStackError(VMError):
    """Wraps openstacksdk errors with a stable error code for API consumers."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


# ── FastAPI exception handlers ────────────────────────────────────────────────

async def vm_not_found_handler(request: Request, exc: VMNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "error": "VM_NOT_FOUND",
            "detail": str(exc),
            "vm_id": exc.vm_id,
        },
    )


async def vm_conflict_handler(request: Request, exc: VMConflictError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "error": "VM_CONFLICT",
            "detail": str(exc),
        },
    )


async def vm_operation_handler(request: Request, exc: VMOperationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "VM_OPERATION_INVALID",
            "detail": str(exc),
            "operation": exc.operation,
        },
    )


async def openstack_error_handler(request: Request, exc: OpenStackError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={
            "error": exc.code,
            "detail": str(exc),
        },
    )
