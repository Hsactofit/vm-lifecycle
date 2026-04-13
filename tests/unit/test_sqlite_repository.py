"""Unit tests for SQLiteVMRepository.

Uses a temporary file-based database per test (via tmp_path fixture)
so tests are fully isolated and cleaned up automatically by pytest.

We test the SQLite repo directly — not through VMService — because we
want to verify persistence behaviour (e.g. data survives reconnection)
which is specific to this implementation.
"""
import pytest

from app.core.exceptions import VMNotFoundError, VMOperationError
from app.models.requests import CreateVMRequest
from app.models.vm import VMStatus
from app.repositories.sqlite_repository import SQLiteVMRepository


def make_repo(tmp_path, build_delay: int = 0) -> SQLiteVMRepository:
    return SQLiteVMRepository(
        db_path=str(tmp_path / "test_vms.db"),
        build_delay_seconds=build_delay,
    )


def make_request(**overrides) -> CreateVMRequest:
    defaults = dict(
        name="sqlite-vm",
        image_id="ubuntu-22.04",
        flavor_id="m1.small",
        network_id="net-001",
    )
    defaults.update(overrides)
    return CreateVMRequest(**defaults)


# ── create / get ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_vm_returns_build_status(tmp_path):
    repo = make_repo(tmp_path, build_delay=9999)
    vm = await repo.create_vm(make_request())
    assert vm.status == VMStatus.BUILD
    assert vm.id is not None


@pytest.mark.asyncio
async def test_get_vm_after_create(tmp_path):
    repo = make_repo(tmp_path, build_delay=9999)
    created = await repo.create_vm(make_request())
    fetched = await repo.get_vm(created.id)
    assert fetched.id == created.id
    assert fetched.name == "sqlite-vm"


@pytest.mark.asyncio
async def test_build_transitions_to_active_after_delay(tmp_path):
    """With build_delay=0, status resolves to ACTIVE on the next read."""
    repo = make_repo(tmp_path, build_delay=0)
    vm = await repo.create_vm(make_request())
    fetched = await repo.get_vm(vm.id)
    assert fetched.status == VMStatus.ACTIVE


@pytest.mark.asyncio
async def test_data_persists_across_reconnect(tmp_path):
    """A new repository instance on the same file should see existing VMs."""
    db_path = str(tmp_path / "persist.db")
    repo1 = SQLiteVMRepository(db_path=db_path, build_delay_seconds=9999)
    vm = await repo1.create_vm(make_request(name="persistent-vm"))

    # New instance — simulates a service restart.
    repo2 = SQLiteVMRepository(db_path=db_path, build_delay_seconds=9999)
    fetched = await repo2.get_vm(vm.id)
    assert fetched.name == "persistent-vm"
    assert fetched.status == VMStatus.BUILD


@pytest.mark.asyncio
async def test_get_nonexistent_raises(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(VMNotFoundError):
        await repo.get_vm("does-not-exist")


# ── delete ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_vm_removes_record(tmp_path):
    repo = make_repo(tmp_path, build_delay=0)
    vm = await repo.create_vm(make_request())
    await repo.get_vm(vm.id)  # ACTIVE
    await repo.delete_vm(vm.id)
    with pytest.raises(VMNotFoundError):
        await repo.get_vm(vm.id)


@pytest.mark.asyncio
async def test_delete_nonexistent_raises(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(VMNotFoundError):
        await repo.delete_vm("ghost-id")


# ── stop / start ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_active_vm(tmp_path):
    repo = make_repo(tmp_path, build_delay=0)
    vm = await repo.create_vm(make_request())
    await repo.get_vm(vm.id)  # trigger ACTIVE
    stopped = await repo.stop_vm(vm.id)
    assert stopped.status == VMStatus.STOPPED


@pytest.mark.asyncio
async def test_start_stopped_vm(tmp_path):
    repo = make_repo(tmp_path, build_delay=0)
    vm = await repo.create_vm(make_request())
    await repo.get_vm(vm.id)      # ACTIVE
    await repo.stop_vm(vm.id)     # STOPPED
    started = await repo.start_vm(vm.id)
    assert started.status == VMStatus.ACTIVE


@pytest.mark.asyncio
async def test_stop_build_vm_raises(tmp_path):
    repo = make_repo(tmp_path, build_delay=9999)
    vm = await repo.create_vm(make_request())
    with pytest.raises(VMOperationError) as exc_info:
        await repo.stop_vm(vm.id)
    assert exc_info.value.operation == "stop"


@pytest.mark.asyncio
async def test_start_active_vm_raises(tmp_path):
    repo = make_repo(tmp_path, build_delay=0)
    vm = await repo.create_vm(make_request())
    await repo.get_vm(vm.id)  # ACTIVE
    with pytest.raises(VMOperationError) as exc_info:
        await repo.start_vm(vm.id)
    assert exc_info.value.operation == "start"
