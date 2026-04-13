"""API integration tests.

These tests run against the full FastAPI application (routing, middleware,
exception handlers, serialisation) but with the repository swapped out for
an in-memory mock. This is the recommended testing pattern for FastAPI apps:
  - Faster than spinning up a real server.
  - Tests the full request/response cycle including middleware.
  - Does not require external dependencies (OpenStack, databases).

Fixtures are defined in tests/conftest.py.
"""
import pytest

# ── Constants ─────────────────────────────────────────────────────────────────

BASE = "/api/v1/vms"

VALID_PAYLOAD = {
    "name": "web-server-01",
    "image_id": "ubuntu-22.04",
    "flavor_id": "m1.small",
    "network_id": "private-net-001",
}


# ── POST /vms ─────────────────────────────────────────────────────────────────

def test_create_vm_returns_201_with_build_status(client):
    response = client.post(BASE, json=VALID_PAYLOAD)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "BUILD"
    assert body["name"] == "web-server-01"
    assert "id" in body
    assert "created_at" in body
    assert "updated_at" in body


def test_create_vm_missing_required_field_returns_422(client):
    response = client.post(BASE, json={"name": "vm-no-image"})
    assert response.status_code == 422


def test_create_vm_blank_name_returns_422(client):
    payload = {**VALID_PAYLOAD, "name": "   "}
    response = client.post(BASE, json=payload)
    assert response.status_code == 422


def test_create_vm_empty_body_returns_422(client):
    response = client.post(BASE, json={})
    assert response.status_code == 422


# ── GET /vms/{vm_id} ──────────────────────────────────────────────────────────

def test_get_vm_returns_200(client):
    create_resp = client.post(BASE, json=VALID_PAYLOAD)
    vm_id = create_resp.json()["id"]
    response = client.get(f"{BASE}/{vm_id}")
    assert response.status_code == 200


def test_get_vm_transitions_to_active(client):
    """With build_delay=0, the first GET should show ACTIVE."""
    vm_id = client.post(BASE, json=VALID_PAYLOAD).json()["id"]
    response = client.get(f"{BASE}/{vm_id}")
    assert response.json()["status"] == "ACTIVE"


def test_get_nonexistent_vm_returns_404(client):
    response = client.get(f"{BASE}/nonexistent-vm-id")
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "VM_NOT_FOUND"
    assert "vm_id" in body


# ── DELETE /vms/{vm_id} ───────────────────────────────────────────────────────

def test_delete_active_vm_returns_204(client):
    vm_id = client.post(BASE, json=VALID_PAYLOAD).json()["id"]
    client.get(f"{BASE}/{vm_id}")  # trigger ACTIVE
    response = client.delete(f"{BASE}/{vm_id}")
    assert response.status_code == 204
    assert response.content == b""


def test_delete_nonexistent_vm_returns_404(client):
    response = client.delete(f"{BASE}/ghost-id")
    assert response.status_code == 404


def test_delete_build_vm_returns_422():
    """Deleting a VM that is still in BUILD state must be rejected with 422.

    Uses a dedicated client with build_delay=9999 so the VM never auto-transitions
    to ACTIVE. We do NOT call GET before DELETE to avoid triggering the lazy
    BUILD→ACTIVE resolution.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api.deps import get_repository
    from app.repositories.mock_repository import MockVMRepository

    slow_repo = MockVMRepository(build_delay_seconds=9999)
    app.dependency_overrides[get_repository] = lambda: slow_repo
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            vm_id = c.post(BASE, json=VALID_PAYLOAD).json()["id"]
            # Delete without calling GET — VM stays in BUILD.
            response = c.delete(f"{BASE}/{vm_id}")
        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "VM_OPERATION_INVALID"
        assert "BUILD" in body["detail"]
        assert body["operation"] == "delete"
    finally:
        app.dependency_overrides.clear()


def test_deleted_vm_not_retrievable(client):
    vm_id = client.post(BASE, json=VALID_PAYLOAD).json()["id"]
    client.get(f"{BASE}/{vm_id}")         # ACTIVE
    client.delete(f"{BASE}/{vm_id}")
    assert client.get(f"{BASE}/{vm_id}").status_code == 404


# ── POST /vms/{vm_id}/stop ────────────────────────────────────────────────────

def test_stop_active_vm_returns_stopped(client):
    vm_id = client.post(BASE, json=VALID_PAYLOAD).json()["id"]
    client.get(f"{BASE}/{vm_id}")  # ACTIVE
    response = client.post(f"{BASE}/{vm_id}/stop")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "STOPPED"
    assert body["action"] == "stop"
    assert body["vm_id"] == vm_id


def test_stop_build_vm_returns_422(client):
    """Cannot stop a VM that hasn't finished provisioning."""
    from app.api.deps import get_repository
    from app.repositories.mock_repository import MockVMRepository
    from app.main import app

    slow_repo = MockVMRepository(build_delay_seconds=9999)
    app.dependency_overrides[get_repository] = lambda: slow_repo
    try:
        vm_id = client.post(BASE, json=VALID_PAYLOAD).json()["id"]
        response = client.post(f"{BASE}/{vm_id}/stop")
        assert response.status_code == 422
        assert response.json()["error"] == "VM_OPERATION_INVALID"
    finally:
        # Restore the original override set by the client fixture.
        from app.repositories.mock_repository import MockVMRepository as MR
        app.dependency_overrides[get_repository] = lambda: MR(build_delay_seconds=0)


def test_stop_nonexistent_vm_returns_404(client):
    response = client.post(f"{BASE}/no-such-vm/stop")
    assert response.status_code == 404


# ── POST /vms/{vm_id}/start ───────────────────────────────────────────────────

def test_start_stopped_vm_returns_active(client):
    vm_id = client.post(BASE, json=VALID_PAYLOAD).json()["id"]
    client.get(f"{BASE}/{vm_id}")          # ACTIVE
    client.post(f"{BASE}/{vm_id}/stop")    # STOPPED
    response = client.post(f"{BASE}/{vm_id}/start")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["action"] == "start"


def test_start_active_vm_returns_422(client):
    vm_id = client.post(BASE, json=VALID_PAYLOAD).json()["id"]
    client.get(f"{BASE}/{vm_id}")  # ACTIVE
    response = client.post(f"{BASE}/{vm_id}/start")
    assert response.status_code == 422


def test_start_nonexistent_vm_returns_404(client):
    response = client.post(f"{BASE}/no-such-vm/start")
    assert response.status_code == 404


# ── Observability ─────────────────────────────────────────────────────────────

def test_response_contains_request_id_header(client):
    response = client.post(BASE, json=VALID_PAYLOAD)
    assert "x-request-id" in response.headers


def test_custom_request_id_is_echoed_back(client):
    custom_id = "trace-abc-123"
    response = client.post(BASE, json=VALID_PAYLOAD, headers={"X-Request-ID": custom_id})
    assert response.headers.get("x-request-id") == custom_id


# ── Health ────────────────────────────────────────────────────────────────────

def test_health_liveness_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert body["backend"] == "mock"


def test_health_readiness_returns_200_for_mock(client):
    """Readiness probe should be 200 for mock and sqlite backends."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["backend"] == "mock"
