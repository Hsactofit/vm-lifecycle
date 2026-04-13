"""Unit tests for VMService.

These tests exercise business logic in isolation — no HTTP, no FastAPI.
The repository is a real MockVMRepository (not a MagicMock) because we
want to test the service's interaction with the repository contract, not
just that certain methods are called. Using a real implementation here is
the "London school vs Detroit school" tradeoff: we prefer Detroit (state-
based) for the service layer since the mock is lightweight and correct.
"""
import pytest

from app.core.exceptions import VMNotFoundError, VMOperationError
from app.models.requests import CreateVMRequest
from app.models.vm import VMStatus
from app.repositories.mock_repository import MockVMRepository
from app.services.vm_service import VMService


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_service(build_delay: int = 0) -> VMService:
    return VMService(MockVMRepository(build_delay_seconds=build_delay))


def make_request(**overrides) -> CreateVMRequest:
    defaults = dict(
        name="test-vm",
        image_id="ubuntu-22.04",
        flavor_id="m1.small",
        network_id="net-001",
    )
    defaults.update(overrides)
    return CreateVMRequest(**defaults)


# ── create_vm ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_vm_returns_build_status():
    """Newly created VMs must start in BUILD state (async provisioning)."""
    service = make_service(build_delay=9999)  # never auto-transitions
    vm = await service.create_vm(make_request())
    assert vm.status == VMStatus.BUILD


@pytest.mark.asyncio
async def test_create_vm_assigns_unique_ids():
    service = make_service()
    vm1 = await service.create_vm(make_request(name="vm-1"))
    vm2 = await service.create_vm(make_request(name="vm-2"))
    assert vm1.id != vm2.id


@pytest.mark.asyncio
async def test_create_vm_preserves_fields():
    service = make_service()
    req = make_request(name="web-01", image_id="centos-9", flavor_id="m2.large", network_id="pub-net")
    vm = await service.create_vm(req)
    assert vm.name == "web-01"
    assert vm.image_id == "centos-9"
    assert vm.flavor_id == "m2.large"
    assert vm.network_id == "pub-net"


# ── get_vm ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_vm_returns_active_after_delay_elapses():
    """With build_delay=0 the status resolves to ACTIVE on first get."""
    service = make_service(build_delay=0)
    vm = await service.create_vm(make_request())
    fetched = await service.get_vm(vm.id)
    assert fetched.status == VMStatus.ACTIVE


@pytest.mark.asyncio
async def test_get_nonexistent_vm_raises_not_found():
    service = make_service()
    with pytest.raises(VMNotFoundError) as exc_info:
        await service.get_vm("does-not-exist")
    assert exc_info.value.vm_id == "does-not-exist"


# ── delete_vm ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_active_vm_succeeds():
    service = make_service(build_delay=0)
    vm = await service.create_vm(make_request())
    await service.get_vm(vm.id)           # trigger BUILD → ACTIVE
    await service.delete_vm(vm.id)        # should not raise
    with pytest.raises(VMNotFoundError):
        await service.get_vm(vm.id)


@pytest.mark.asyncio
async def test_delete_build_vm_raises_operation_error():
    """The service must guard against deleting mid-provisioning VMs."""
    service = make_service(build_delay=9999)
    vm = await service.create_vm(make_request())
    assert vm.status == VMStatus.BUILD
    with pytest.raises(VMOperationError) as exc_info:
        await service.delete_vm(vm.id)
    assert exc_info.value.operation == "delete"


@pytest.mark.asyncio
async def test_delete_nonexistent_vm_raises_not_found():
    service = make_service()
    with pytest.raises(VMNotFoundError):
        await service.delete_vm("ghost-vm-id")


# ── stop_vm ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_active_vm_transitions_to_stopped():
    service = make_service(build_delay=0)
    vm = await service.create_vm(make_request())
    await service.get_vm(vm.id)   # BUILD → ACTIVE
    stopped = await service.stop_vm(vm.id)
    assert stopped.status == VMStatus.STOPPED


@pytest.mark.asyncio
async def test_stop_build_vm_raises_operation_error():
    service = make_service(build_delay=9999)
    vm = await service.create_vm(make_request())
    with pytest.raises(VMOperationError) as exc_info:
        await service.stop_vm(vm.id)
    assert exc_info.value.operation == "stop"


@pytest.mark.asyncio
async def test_stop_already_stopped_vm_raises_operation_error():
    service = make_service(build_delay=0)
    vm = await service.create_vm(make_request())
    await service.get_vm(vm.id)   # ACTIVE
    await service.stop_vm(vm.id)  # STOPPED
    with pytest.raises(VMOperationError):
        await service.stop_vm(vm.id)  # should reject


# ── start_vm ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_stopped_vm_transitions_to_active():
    service = make_service(build_delay=0)
    vm = await service.create_vm(make_request())
    await service.get_vm(vm.id)   # ACTIVE
    await service.stop_vm(vm.id)  # STOPPED
    started = await service.start_vm(vm.id)
    assert started.status == VMStatus.ACTIVE


@pytest.mark.asyncio
async def test_start_active_vm_raises_operation_error():
    service = make_service(build_delay=0)
    vm = await service.create_vm(make_request())
    await service.get_vm(vm.id)   # ACTIVE
    with pytest.raises(VMOperationError) as exc_info:
        await service.start_vm(vm.id)
    assert exc_info.value.operation == "start"


@pytest.mark.asyncio
async def test_start_build_vm_raises_operation_error():
    service = make_service(build_delay=9999)
    vm = await service.create_vm(make_request())
    with pytest.raises(VMOperationError):
        await service.start_vm(vm.id)


# ── full lifecycle ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_lifecycle_build_active_stopped_active_deleted():
    service = make_service(build_delay=0)
    req = make_request(name="lifecycle-vm")

    vm = await service.create_vm(req)
    assert vm.status == VMStatus.BUILD

    vm = await service.get_vm(vm.id)
    assert vm.status == VMStatus.ACTIVE

    vm = await service.stop_vm(vm.id)
    assert vm.status == VMStatus.STOPPED

    vm = await service.start_vm(vm.id)
    assert vm.status == VMStatus.ACTIVE

    await service.delete_vm(vm.id)
    with pytest.raises(VMNotFoundError):
        await service.get_vm(vm.id)
