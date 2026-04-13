"""In-memory VM repository with simulated async lifecycle.

This is the default backend — no OpenStack environment required.

Lifecycle simulation strategy
──────────────────────────────
OpenStack VM creation is genuinely asynchronous: Nova returns immediately
with status=BUILD and the caller must poll until the server reaches ACTIVE.

We replicate this pattern without background threads by using *lazy status
resolution*: the BUILD → ACTIVE transition is computed on the next read
based on how much time has elapsed since creation. This keeps the mock
stateless between calls and avoids race conditions inherent in background
task approaches.

                 create_vm()          get_vm() after MOCK_BUILD_DELAY_SECONDS
                     │                          │
  ┌──────┐  BUILD    ▼                 ACTIVE   ▼   STOPPED
  │ POST │ ──────► [store]  ─── time ────────► [store] ◄──── stop_vm()
  └──────┘                                             ────► [store]  start_vm()
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict

from app.core.exceptions import VMNotFoundError, VMOperationError
from app.models.requests import CreateVMRequest
from app.models.vm import VMRecord, VMStatus
from app.repositories.base import VMRepository

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MockVMRepository(VMRepository):
    """Thread-safe*-ish in-memory store.

    *FastAPI runs on a single event-loop thread by default so the GIL
    provides adequate safety here. A production async datastore (Redis,
    DynamoDB) would replace this with real concurrency guarantees.
    """

    def __init__(self, build_delay_seconds: int = 10) -> None:
        self._store: Dict[str, VMRecord] = {}
        self._build_delay = build_delay_seconds

    # ── Private helpers ───────────────────────────────────────────────────────

    def _resolve_build(self, record: VMRecord) -> VMRecord:
        """Lazily transition BUILD → ACTIVE once the delay has elapsed."""
        if record.status != VMStatus.BUILD:
            return record

        elapsed = (_utcnow() - record.created_at).total_seconds()
        if elapsed >= self._build_delay:
            record.status = VMStatus.ACTIVE
            record.updated_at = _utcnow()
            self._store[record.id] = record
            logger.info(
                "vm_status_transitioned",
                extra={"vm_id": record.id, "from_status": "BUILD", "to_status": "ACTIVE"},
            )
        return record

    def _get_or_raise(self, vm_id: str) -> VMRecord:
        record = self._store.get(vm_id)
        if record is None:
            raise VMNotFoundError(vm_id)
        return self._resolve_build(record)

    # ── VMRepository interface ────────────────────────────────────────────────

    async def create_vm(self, request: CreateVMRequest) -> VMRecord:
        vm = VMRecord(
            id=str(uuid.uuid4()),
            name=request.name,
            image_id=request.image_id,
            flavor_id=request.flavor_id,
            network_id=request.network_id,
            status=VMStatus.BUILD,
        )
        self._store[vm.id] = vm
        logger.info("vm_created", extra={"vm_id": vm.id, "vm_name": vm.name, "status": vm.status})
        return vm

    async def get_vm(self, vm_id: str) -> VMRecord:
        record = self._get_or_raise(vm_id)
        logger.debug("vm_fetched", extra={"vm_id": vm_id, "status": record.status})
        return record

    async def delete_vm(self, vm_id: str) -> None:
        self._get_or_raise(vm_id)  # raises VMNotFoundError if missing
        del self._store[vm_id]
        logger.info("vm_deleted", extra={"vm_id": vm_id})

    async def start_vm(self, vm_id: str) -> VMRecord:
        record = self._get_or_raise(vm_id)
        if record.status != VMStatus.STOPPED:
            raise VMOperationError(
                vm_id,
                "start",
                f"VM is in '{record.status}' state — only STOPPED VMs can be started",
            )
        record.status = VMStatus.ACTIVE
        record.updated_at = _utcnow()
        self._store[vm_id] = record
        logger.info("vm_started", extra={"vm_id": vm_id, "status": record.status})
        return record

    async def stop_vm(self, vm_id: str) -> VMRecord:
        record = self._get_or_raise(vm_id)
        if record.status != VMStatus.ACTIVE:
            raise VMOperationError(
                vm_id,
                "stop",
                f"VM is in '{record.status}' state — only ACTIVE VMs can be stopped",
            )
        record.status = VMStatus.STOPPED
        record.updated_at = _utcnow()
        self._store[vm_id] = record
        logger.info("vm_stopped", extra={"vm_id": vm_id, "status": record.status})
        return record
