"""FastAPI dependency providers.

Using FastAPI's Depends() system gives us three things for free:
  1. Easy unit-test overrides via app.dependency_overrides
  2. Lazy initialisation — the repository is only constructed on first request
  3. A single place to change wiring (add connection pooling, metrics, etc.)

Repository singletons
──────────────────────
The mock and SQLite repositories are module-level singletons so all
requests within a process share the same state — matching what a real
database provides. Tests override get_repository with a fresh instance
per test to prevent state bleed between test cases.

The OpenStack repository is intentionally NOT a singleton: it holds an
SDK connection object that may expire, and reconnecting per-request is
safer for long-running services (a connection pool would be the production
solution here).
"""
from functools import cache

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.repositories.base import VMRepository
from app.repositories.mock_repository import MockVMRepository
from app.services.vm_service import VMService


@cache
def _mock_singleton(build_delay_seconds: int) -> MockVMRepository:
    """One in-memory store per process (same semantics as a real DB)."""
    return MockVMRepository(build_delay_seconds=build_delay_seconds)


@cache
def _sqlite_singleton(db_path: str, build_delay_seconds: int):
    """One SQLite repository per process; the db file is the shared state."""
    from app.repositories.sqlite_repository import SQLiteVMRepository
    return SQLiteVMRepository(db_path=db_path, build_delay_seconds=build_delay_seconds)


@cache
def _openstack_singleton():
    """One OpenStack repository per process.

    Constructing OpenStackVMRepository calls openstack.connect() which
    performs Keystone auth. Caching means auth happens once at startup,
    not on every request — avoids per-request latency and prevents
    transient Keystone failures from failing individual API calls.
    """
    from app.repositories.openstack_repository import OpenStackVMRepository
    return OpenStackVMRepository()


def get_repository(settings: Settings = Depends(get_settings)) -> VMRepository:
    """Return the configured repository implementation.

    Controlled by the BACKEND environment variable:
      BACKEND=mock      → MockVMRepository     (in-memory, default)
      BACKEND=sqlite    → SQLiteVMRepository   (file-based, survives restarts)
      BACKEND=openstack → OpenStackVMRepository (real cloud, requires credentials)
    """
    if settings.BACKEND == "openstack":
        return _openstack_singleton()

    if settings.BACKEND == "sqlite":
        return _sqlite_singleton(
            settings.SQLITE_DB_PATH,
            settings.SQLITE_BUILD_DELAY_SECONDS,
        )

    return _mock_singleton(settings.MOCK_BUILD_DELAY_SECONDS)


def get_vm_service(
    repo: VMRepository = Depends(get_repository),
) -> VMService:
    return VMService(repo)


def get_request_id(request: Request) -> str:
    """Extract the request_id set by RequestTracingMiddleware."""
    return getattr(request.state, "request_id", "-")
