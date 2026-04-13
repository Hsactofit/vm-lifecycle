"""Real OpenStack repository using openstacksdk.

Switching from the mock backend requires only setting BACKEND=openstack and
providing credentials — no code changes anywhere else.

OpenStack service mapping
──────────────────────────
  Operation      Nova (Compute) call          Neutron / Cinder involvement
  ─────────────────────────────────────────────────────────────────────────
  create_vm   →  create_server                network_id → Neutron port
  get_vm      →  get_server                   (status only)
  delete_vm   →  delete_server                detaches ports automatically
  start_vm    →  start_server                 –
  stop_vm     →  stop_server                  –

  Future:
  attach_volume → attach_volume_to_server     Cinder volume_id required
  create_port   → create_port                 Neutron static IPs

Concurrency model
──────────────────
openstacksdk is synchronous. All SDK calls are wrapped in asyncio.to_thread()
so blocking HTTP I/O runs in a thread-pool worker instead of stalling the
FastAPI event loop. Under high concurrency this means one slow OpenStack call
cannot starve unrelated requests.

Singleton connection
─────────────────────
The repository is constructed once via @cache in deps.py. The SDK connection
(which performs Keystone auth on first use) is re-used across all requests,
amortising session setup cost and avoiding repeated auth round-trips.

Lifecycle action state model
──────────────────────────────
Nova lifecycle actions (start, stop) are asynchronous. Fetching server state
immediately after issuing an action often returns the *old* state because Nova
hasn't processed the request yet. We therefore:
  1. Fetch current state BEFORE the action (validates it's legal).
  2. Issue the action.
  3. Return a record with the EXPECTED target status set explicitly.
  4. Callers are directed to poll GET /vms/{id} for confirmed state.

This is the correct production contract — identical to what OpenStack's own
Dashboard does, and what the mock already models.

Retry / resilience notes (production TODOs)
────────────────────────────────────────────
* Add tenacity with exponential backoff on transient 503s from Nova.
* Pass client_request_id header on create_server for idempotency on retries.
* For bounded polling (accept latency in exchange for confirmed state),
  use conn.compute.wait_for_server(server, status="ACTIVE", timeout=300).
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.config import get_settings
from app.core.exceptions import OpenStackError, VMNotFoundError, VMOperationError
from app.models.requests import CreateVMRequest
from app.models.vm import VMRecord, VMStatus
from app.repositories.base import VMRepository

logger = logging.getLogger(__name__)

# Map Nova server statuses → domain VMStatus.
# Reference: https://docs.openstack.org/api-guide/compute/server_concepts.html
_NOVA_STATUS_MAP: "dict[str, VMStatus]" = {
    "BUILD": VMStatus.BUILD,
    "ACTIVE": VMStatus.ACTIVE,
    "SHUTOFF": VMStatus.STOPPED,   # Nova uses SHUTOFF; we expose STOPPED
    "DELETED": VMStatus.DELETED,
    "ERROR": VMStatus.ERROR,
    "PAUSED": VMStatus.STOPPED,
    "SUSPENDED": VMStatus.STOPPED,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OpenStackVMRepository(VMRepository):
    """Connects to OpenStack via openstacksdk.

    Constructed once (singleton via @cache in deps.py). The SDK connection
    object is thread-safe for concurrent use from asyncio.to_thread workers.

    Auth priority (openstacksdk resolves in this order):
      1. clouds.yaml  (recommended — see clouds.yaml.example)
      2. OS_* environment variables

    Install the optional dependency first:
        pip install openstacksdk
        # or: pip install -r requirements-openstack.txt
    """

    def __init__(self) -> None:
        try:
            import openstack  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "openstacksdk is not installed. "
                "Run: pip install openstacksdk  (or pip install -r requirements-openstack.txt)"
            ) from exc

        settings = get_settings()
        try:
            self._conn = openstack.connect(cloud=settings.OS_CLOUD)
            logger.info("openstack_connected", extra={"cloud": settings.OS_CLOUD})
        except Exception as exc:
            raise OpenStackError("OPENSTACK_CONNECTION_FAILED", str(exc)) from exc

    # ── Private helpers ───────────────────────────────────────────────────────

    def _to_record(self, server) -> VMRecord:
        """Convert an openstacksdk Server resource to our domain VMRecord."""
        raw_status = getattr(server, "status", "ERROR")
        status = _NOVA_STATUS_MAP.get(raw_status, VMStatus.ERROR)

        def _parse_dt(s: Optional[str]) -> datetime:
            if not s:
                return _utcnow()
            return datetime.fromisoformat(s.replace("Z", "+00:00"))

        return VMRecord(
            id=server.id,
            name=server.name,
            image_id=(server.image or {}).get("id", ""),
            flavor_id=(server.flavor or {}).get("id", ""),
            network_id="",  # populated from addresses if needed
            status=status,
            created_at=_parse_dt(getattr(server, "created_at", None)),
            updated_at=_parse_dt(getattr(server, "updated_at", None)),
        )

    def _handle_exc(self, exc: Exception, vm_id: Optional[str] = None) -> None:
        """Map openstacksdk exceptions → domain exceptions.

        Keeps OpenStack-specific vocabulary out of the service layer so the
        API returns consistent error codes regardless of backend.
        """
        name = type(exc).__name__
        msg = str(exc)

        if "ResourceNotFound" in name or "404" in msg:
            raise VMNotFoundError(vm_id or "unknown") from exc
        if "Conflict" in name or "409" in msg:
            raise OpenStackError("OPENSTACK_CONFLICT", msg) from exc
        if "OverLimit" in name or "403" in msg:
            raise OpenStackError("OPENSTACK_QUOTA_EXCEEDED", msg) from exc
        if "BadRequest" in name or "400" in msg:
            raise OpenStackError("OPENSTACK_BAD_REQUEST", msg) from exc
        raise OpenStackError("OPENSTACK_ERROR", msg) from exc

    def _sync_create(self, request: CreateVMRequest):
        return self._conn.compute.create_server(
            name=request.name,
            image_id=request.image_id,
            flavor_id=request.flavor_id,
            networks=[{"uuid": request.network_id}],
            # Production: add key_name, security_groups, user_data here.
        )

    def _sync_get(self, vm_id: str):
        return self._conn.compute.get_server(vm_id)

    def _sync_delete(self, vm_id: str) -> None:
        self._conn.compute.delete_server(vm_id)

    def _sync_start(self, vm_id: str) -> None:
        self._conn.compute.start_server(vm_id)

    def _sync_stop(self, vm_id: str) -> None:
        self._conn.compute.stop_server(vm_id)

    def ping(self) -> bool:
        """Lightweight connectivity check for the readiness probe.

        Lists at most one server — cheap and exercises the full auth path.
        Returns True if the Nova API is reachable, raises on failure.
        """
        next(iter(self._conn.compute.servers(limit=1)), None)
        return True

    # ── VMRepository interface ────────────────────────────────────────────────

    async def create_vm(self, request: CreateVMRequest) -> VMRecord:
        try:
            server = await asyncio.to_thread(self._sync_create, request)
            logger.info("openstack_vm_created", extra={"vm_id": server.id, "vm_name": server.name})
            return self._to_record(server)
        except (VMNotFoundError, OpenStackError):
            raise
        except Exception as exc:
            self._handle_exc(exc)

    async def get_vm(self, vm_id: str) -> VMRecord:
        try:
            server = await asyncio.to_thread(self._sync_get, vm_id)
            return self._to_record(server)
        except (VMNotFoundError, OpenStackError):
            raise
        except Exception as exc:
            self._handle_exc(exc, vm_id)

    async def delete_vm(self, vm_id: str) -> None:
        try:
            await asyncio.to_thread(self._sync_delete, vm_id)
            logger.info("openstack_vm_deleted", extra={"vm_id": vm_id})
        except (VMNotFoundError, OpenStackError):
            raise
        except Exception as exc:
            self._handle_exc(exc, vm_id)

    async def start_vm(self, vm_id: str) -> VMRecord:
        """Issue a start action and return the expected target state.

        Nova actions are async: fetching server state immediately after
        start_server often returns the old (SHUTOFF) status. We therefore
        fetch state BEFORE the action and return the expected target status
        (ACTIVE) explicitly. Callers should poll GET /vms/{id} to confirm.
        """
        try:
            # Validate current state and capture existing metadata.
            server = await asyncio.to_thread(self._sync_get, vm_id)
            record = self._to_record(server)
            # Issue the action.
            await asyncio.to_thread(self._sync_start, vm_id)
            # Return the intended target state — not a stale re-fetch.
            record.status = VMStatus.ACTIVE
            record.updated_at = _utcnow()
            logger.info("openstack_vm_start_issued", extra={"vm_id": vm_id, "target_status": "ACTIVE"})
            return record
        except (VMNotFoundError, OpenStackError, VMOperationError):
            raise
        except Exception as exc:
            self._handle_exc(exc, vm_id)

    async def stop_vm(self, vm_id: str) -> VMRecord:
        """Issue a stop action and return the expected target state.

        Same async-action contract as start_vm: fetch first, act second,
        return expected target state (STOPPED). Poll for confirmed state.
        """
        try:
            server = await asyncio.to_thread(self._sync_get, vm_id)
            record = self._to_record(server)
            await asyncio.to_thread(self._sync_stop, vm_id)
            record.status = VMStatus.STOPPED
            record.updated_at = _utcnow()
            logger.info("openstack_vm_stop_issued", extra={"vm_id": vm_id, "target_status": "STOPPED"})
            return record
        except (VMNotFoundError, OpenStackError, VMOperationError):
            raise
        except Exception as exc:
            self._handle_exc(exc, vm_id)
