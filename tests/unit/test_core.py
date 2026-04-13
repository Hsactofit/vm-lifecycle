"""Unit tests for core layer — exceptions, logging, config.

Covers branches that the API/service tests can't easily reach:
  - VMConflictError and OpenStackError HTTP handlers
  - RotatingFileHandler setup in logging_config
  - SQLite backend branch in deps.get_repository
"""
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import (
    OpenStackError,
    VMConflictError,
    VMNotFoundError,
    VMOperationError,
    openstack_error_handler,
    vm_conflict_handler,
    vm_not_found_handler,
    vm_operation_handler,
)
from app.core.logging_config import setup_logging


# ── Exception class construction ──────────────────────────────────────────────

def test_vm_conflict_error_stores_vm_id():
    err = VMConflictError("vm-abc", "already exists")
    assert err.vm_id == "vm-abc"
    assert "already exists" in str(err)


def test_openstack_error_stores_code():
    err = OpenStackError("OPENSTACK_QUOTA_EXCEEDED", "quota hit")
    assert err.code == "OPENSTACK_QUOTA_EXCEEDED"
    assert "quota hit" in str(err)


def test_vm_operation_error_stores_fields():
    err = VMOperationError("vm-1", "start", "VM is in BUILD state")
    assert err.vm_id == "vm-1"
    assert err.operation == "start"
    assert "start" in str(err)


# ── Exception handlers ────────────────────────────────────────────────────────

def _mock_request():
    return MagicMock()


@pytest.mark.asyncio
async def test_vm_not_found_handler_returns_404():
    exc = VMNotFoundError("vm-xyz")
    resp = await vm_not_found_handler(_mock_request(), exc)
    assert resp.status_code == 404
    import json
    body = json.loads(resp.body)
    assert body["error"] == "VM_NOT_FOUND"
    assert body["vm_id"] == "vm-xyz"


@pytest.mark.asyncio
async def test_vm_conflict_handler_returns_409():
    exc = VMConflictError("vm-xyz", "conflict occurred")
    resp = await vm_conflict_handler(_mock_request(), exc)
    assert resp.status_code == 409
    import json
    body = json.loads(resp.body)
    assert body["error"] == "VM_CONFLICT"


@pytest.mark.asyncio
async def test_vm_operation_handler_returns_422():
    exc = VMOperationError("vm-xyz", "stop", "not active")
    resp = await vm_operation_handler(_mock_request(), exc)
    assert resp.status_code == 422
    import json
    body = json.loads(resp.body)
    assert body["error"] == "VM_OPERATION_INVALID"
    assert body["operation"] == "stop"


@pytest.mark.asyncio
async def test_openstack_error_handler_returns_502():
    exc = OpenStackError("OPENSTACK_QUOTA_EXCEEDED", "out of quota")
    resp = await openstack_error_handler(_mock_request(), exc)
    assert resp.status_code == 502
    import json
    body = json.loads(resp.body)
    assert body["error"] == "OPENSTACK_QUOTA_EXCEEDED"
    assert "out of quota" in body["detail"]


# ── Logging: file handler setup ───────────────────────────────────────────────

def test_setup_logging_creates_log_file(tmp_path, monkeypatch):
    """When LOG_FILE is configured, setup_logging should add a RotatingFileHandler."""
    log_file = str(tmp_path / "test_app.log")

    # Patch settings so setup_logging sees our temp file.
    from app.core import config as cfg
    cfg.get_settings.cache_clear()

    monkeypatch.setenv("LOG_FILE", log_file)
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    cfg.get_settings.cache_clear()

    setup_logging()

    root = logging.getLogger()
    handler_types = [type(h).__name__ for h in root.handlers]
    assert "RotatingFileHandler" in handler_types
    assert Path(log_file).exists()

    # Cleanup: restore default logging so other tests aren't affected.
    cfg.get_settings.cache_clear()
    monkeypatch.delenv("LOG_FILE", raising=False)
    cfg.get_settings.cache_clear()
    setup_logging()


def test_setup_logging_text_format(monkeypatch):
    """LOG_FORMAT=text should use a plain Formatter, not JSONFormatter."""
    from app.core import config as cfg
    from app.core.logging_config import JSONFormatter

    cfg.get_settings.cache_clear()
    monkeypatch.setenv("LOG_FORMAT", "text")
    monkeypatch.delenv("LOG_FILE", raising=False)
    cfg.get_settings.cache_clear()

    setup_logging()

    root = logging.getLogger()
    stdout_handler = root.handlers[0]
    assert not isinstance(stdout_handler.formatter, JSONFormatter)

    # Restore
    cfg.get_settings.cache_clear()
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    cfg.get_settings.cache_clear()
    setup_logging()


# ── Deps: SQLite backend branch ───────────────────────────────────────────────

def test_get_repository_returns_sqlite_repo(tmp_path, monkeypatch):
    """BACKEND=sqlite should return a SQLiteVMRepository instance."""
    from app.core import config as cfg
    from app.api.deps import _sqlite_singleton

    # Clear caches so env changes take effect.
    cfg.get_settings.cache_clear()
    _sqlite_singleton.cache_clear()

    monkeypatch.setenv("BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "dep_test.db"))
    cfg.get_settings.cache_clear()

    from app.api.deps import get_repository
    from app.repositories.sqlite_repository import SQLiteVMRepository

    settings = cfg.get_settings()
    repo = get_repository(settings)
    assert isinstance(repo, SQLiteVMRepository)

    # Restore
    cfg.get_settings.cache_clear()
    _sqlite_singleton.cache_clear()
    monkeypatch.delenv("BACKEND", raising=False)
    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    cfg.get_settings.cache_clear()
