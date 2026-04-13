"""Shared pytest fixtures.

Design decisions
─────────────────
* build_delay_seconds=0 in all test fixtures so the mock immediately
  transitions BUILD → ACTIVE on the first get_vm call. This keeps tests
  deterministic and fast without threading or asyncio.sleep().

* Each test that needs API-level testing gets its own MockVMRepository
  instance (fresh_repo fixture) injected via app.dependency_overrides,
  preventing any state bleed between tests.
"""
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.api.deps import get_repository
from app.main import app
from app.models.requests import CreateVMRequest
from app.repositories.mock_repository import MockVMRepository
from app.services.vm_service import VMService


@pytest.fixture
def mock_repo() -> MockVMRepository:
    """Zero-delay mock repo for service-layer unit tests."""
    return MockVMRepository(build_delay_seconds=0)


@pytest.fixture
def vm_service(mock_repo: MockVMRepository) -> VMService:
    return VMService(mock_repo)


@pytest.fixture
def sample_request() -> CreateVMRequest:
    return CreateVMRequest(
        name="test-vm",
        image_id="ubuntu-22.04",
        flavor_id="m1.small",
        network_id="private-net-001",
    )


@pytest.fixture
def client(mock_repo: MockVMRepository) -> TestClient:
    """API test client with dependency override for isolation."""
    app.dependency_overrides[get_repository] = lambda: mock_repo
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()
