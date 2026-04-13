"""VM lifecycle service — the business logic layer.

Responsibilities
─────────────────
* Enforce cross-cutting business rules that are not specific to a backend
  (e.g. "you cannot delete a VM that is still building").
* Orchestrate repository calls and translate domain objects into return values.
* Attach observability context (request_id, vm_id) to every log statement.

What does NOT belong here
──────────────────────────
* HTTP concerns (status codes, headers) — that is the API layer's job.
* Storage mechanics (dict manipulation, OpenStack SDK calls) — that is
  the repository layer's job.

This separation means the service can be tested with any VMRepository
implementation — including a fully-controlled test double — without
spinning up FastAPI or touching a network.
"""
import logging

from app.core.exceptions import VMOperationError
from app.models.requests import CreateVMRequest
from app.models.vm import VMRecord, VMStatus
from app.repositories.base import VMRepository

logger = logging.getLogger(__name__)


class VMService:
    def __init__(self, repository: VMRepository) -> None:
        self._repo = repository

    # ── Public API ────────────────────────────────────────────────────────────

    async def create_vm(
        self, request: CreateVMRequest, *, request_id: str = "-"
    ) -> VMRecord:
        logger.info(
            "create_vm_requested",
            extra={
                "request_id": request_id,
                "vm_name": request.name,
                "image_id": request.image_id,
                "flavor_id": request.flavor_id,
                "network_id": request.network_id,
            },
        )
        vm = await self._repo.create_vm(request)
        logger.info(
            "create_vm_succeeded",
            extra={"request_id": request_id, "vm_id": vm.id, "status": vm.status},
        )
        return vm

    async def get_vm(self, vm_id: str, *, request_id: str = "-") -> VMRecord:
        logger.info("get_vm_requested", extra={"request_id": request_id, "vm_id": vm_id})
        vm = await self._repo.get_vm(vm_id)
        logger.info(
            "get_vm_succeeded",
            extra={"request_id": request_id, "vm_id": vm_id, "status": vm.status},
        )
        return vm

    async def delete_vm(self, vm_id: str, *, request_id: str = "-") -> None:
        logger.info("delete_vm_requested", extra={"request_id": request_id, "vm_id": vm_id})

        # Business rule: deleting a VM mid-provisioning could leave orphaned
        # resources on the hypervisor. Reject and let the caller retry after
        # the VM reaches a stable state.
        vm = await self._repo.get_vm(vm_id)
        if vm.status == VMStatus.BUILD:
            raise VMOperationError(
                vm_id,
                "delete",
                "VM is still provisioning (BUILD). Wait for ACTIVE or ERROR before deleting.",
            )

        await self._repo.delete_vm(vm_id)
        logger.info("delete_vm_succeeded", extra={"request_id": request_id, "vm_id": vm_id})

    async def start_vm(self, vm_id: str, *, request_id: str = "-") -> VMRecord:
        logger.info("start_vm_requested", extra={"request_id": request_id, "vm_id": vm_id})
        vm = await self._repo.start_vm(vm_id)
        logger.info(
            "start_vm_succeeded",
            extra={"request_id": request_id, "vm_id": vm_id, "status": vm.status},
        )
        return vm

    async def stop_vm(self, vm_id: str, *, request_id: str = "-") -> VMRecord:
        logger.info("stop_vm_requested", extra={"request_id": request_id, "vm_id": vm_id})
        vm = await self._repo.stop_vm(vm_id)
        logger.info(
            "stop_vm_succeeded",
            extra={"request_id": request_id, "vm_id": vm_id, "status": vm.status},
        )
        return vm
