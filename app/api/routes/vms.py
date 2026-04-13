"""VM lifecycle endpoints.

Route → Service mapping
────────────────────────
  POST   /api/v1/vms           → VMService.create_vm
  GET    /api/v1/vms/{vm_id}   → VMService.get_vm
  DELETE /api/v1/vms/{vm_id}   → VMService.delete_vm
  POST   /api/v1/vms/{vm_id}/start → VMService.start_vm
  POST   /api/v1/vms/{vm_id}/stop  → VMService.stop_vm

All routes are thin: they validate input (Pydantic), call the service,
and serialise the result. No business logic lives here.
"""
from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_request_id, get_vm_service
from app.models.requests import CreateVMRequest
from app.models.responses import ActionResponse, VMResponse
from app.models.vm import VMRecord
from app.services.vm_service import VMService

router = APIRouter(prefix="/vms", tags=["VM Lifecycle"])


def _to_vm_response(vm: VMRecord) -> VMResponse:
    return VMResponse(
        id=vm.id,
        name=vm.name,
        image_id=vm.image_id,
        flavor_id=vm.flavor_id,
        network_id=vm.network_id,
        status=vm.status,
        created_at=vm.created_at,
        updated_at=vm.updated_at,
    )


@router.post(
    "",
    response_model=VMResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new VM",
    description=(
        "Initiates VM provisioning. Returns **status=BUILD** immediately. "
        "Poll `GET /api/v1/vms/{id}` until status transitions to **ACTIVE**. "
        "This mirrors the async nature of OpenStack Nova."
    ),
    responses={
        422: {"description": "Validation error in request body"},
    },
)
async def create_vm(
    body: CreateVMRequest,
    service: VMService = Depends(get_vm_service),
    request_id: str = Depends(get_request_id),
) -> VMResponse:
    vm = await service.create_vm(body, request_id=request_id)
    return _to_vm_response(vm)


@router.get(
    "/{vm_id}",
    response_model=VMResponse,
    summary="Get VM details",
    description=(
        "Returns the current state of a VM. "
        "Status transitions from BUILD → ACTIVE happen automatically over time."
    ),
    responses={
        404: {"description": "VM not found"},
    },
)
async def get_vm(
    vm_id: str,
    service: VMService = Depends(get_vm_service),
    request_id: str = Depends(get_request_id),
) -> VMResponse:
    vm = await service.get_vm(vm_id, request_id=request_id)
    return _to_vm_response(vm)


@router.delete(
    "/{vm_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a VM",
    description=(
        "Permanently deletes a VM. "
        "Returns 422 if the VM is still in BUILD state — "
        "wait for ACTIVE or ERROR before deleting."
    ),
    responses={
        404: {"description": "VM not found"},
        422: {"description": "VM cannot be deleted in its current state"},
    },
)
async def delete_vm(
    vm_id: str,
    service: VMService = Depends(get_vm_service),
    request_id: str = Depends(get_request_id),
) -> Response:
    await service.delete_vm(vm_id, request_id=request_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{vm_id}/start",
    response_model=ActionResponse,
    summary="Start a stopped VM",
    description="Transitions a VM from STOPPED → ACTIVE.",
    responses={
        404: {"description": "VM not found"},
        422: {"description": "VM is not in STOPPED state"},
    },
)
async def start_vm(
    vm_id: str,
    service: VMService = Depends(get_vm_service),
    request_id: str = Depends(get_request_id),
) -> ActionResponse:
    vm = await service.start_vm(vm_id, request_id=request_id)
    return ActionResponse(
        vm_id=vm.id,
        action="start",
        status=vm.status,
        message="VM start initiated successfully",
    )


@router.post(
    "/{vm_id}/stop",
    response_model=ActionResponse,
    summary="Stop a running VM",
    description="Transitions a VM from ACTIVE → STOPPED.",
    responses={
        404: {"description": "VM not found"},
        422: {"description": "VM is not in ACTIVE state"},
    },
)
async def stop_vm(
    vm_id: str,
    service: VMService = Depends(get_vm_service),
    request_id: str = Depends(get_request_id),
) -> ActionResponse:
    vm = await service.stop_vm(vm_id, request_id=request_id)
    return ActionResponse(
        vm_id=vm.id,
        action="stop",
        status=vm.status,
        message="VM stop initiated successfully",
    )
