"""SQLite-backed VM repository.

Provides durable persistence without requiring a database server.
Uses Python's built-in sqlite3 module wrapped in asyncio.to_thread()
so blocking I/O never stalls the FastAPI event loop.

VMs are stored as JSON blobs in a single table — intentionally simple
for a proof-of-concept. A production system would use SQLAlchemy with
async drivers (asyncpg for PostgreSQL) and proper column types.

Schema
──────
  vms(id TEXT PK, data TEXT, status TEXT, created_at TEXT, updated_at TEXT)

The `status` and timestamp columns are extracted to top-level columns so
that future queries (filter by status, time-range scans) don't require
JSON parsing in the database.

Lifecycle simulation
────────────────────
Same lazy-resolution strategy as MockVMRepository: BUILD → ACTIVE
transition is computed at read time based on elapsed seconds.
The `created_at` value stored in the DB is the authoritative clock.

Switching to this backend
──────────────────────────
  BACKEND=sqlite
  SQLITE_DB_PATH=./data/vms.db   (created automatically)
"""
import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.core.exceptions import VMNotFoundError, VMOperationError
from app.models.requests import CreateVMRequest
from app.models.vm import VMRecord, VMStatus
from app.repositories.base import VMRepository

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vms (
    id          TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _record_from_row(row: sqlite3.Row) -> VMRecord:
    data = json.loads(row["data"])
    return VMRecord(**data)


class SQLiteVMRepository(VMRepository):
    """Persistent VM repository backed by a local SQLite database file.

    Suitable for single-node deployments, development, and demos.
    Replace with PostgreSQL (via asyncpg + SQLAlchemy) for multi-node.
    """

    def __init__(self, db_path: Optional[str] = None, build_delay_seconds: int = 10) -> None:
        settings = get_settings()
        self._db_path = db_path or settings.SQLITE_DB_PATH
        self._build_delay = build_delay_seconds
        # Ensure parent directory exists.
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # Run DDL synchronously at init — happens once at startup.
        self._init_db()
        logger.info("sqlite_repository_ready", extra={"db_path": self._db_path})

    # ── Sync helpers (run via asyncio.to_thread) ──────────────────────────────

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.commit()

    def _resolve_build(self, record: VMRecord) -> VMRecord:
        """Lazily transition BUILD → ACTIVE if enough time has elapsed."""
        if record.status != VMStatus.BUILD:
            return record
        elapsed = (_utcnow() - record.created_at).total_seconds()
        if elapsed >= self._build_delay:
            record.status = VMStatus.ACTIVE
            record.updated_at = _utcnow()
            # Persist updated status synchronously (caller is already in thread).
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE vms SET status=?, data=?, updated_at=? WHERE id=?",
                    (
                        record.status,
                        record.model_dump_json(),
                        record.updated_at.isoformat(),
                        record.id,
                    ),
                )
                conn.commit()
            logger.info(
                "vm_status_transitioned",
                extra={"vm_id": record.id, "from_status": "BUILD", "to_status": "ACTIVE"},
            )
        return record

    def _sync_create(self, record: VMRecord) -> VMRecord:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO vms (id, data, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                (
                    record.id,
                    record.model_dump_json(),
                    record.status,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            conn.commit()
        return record

    def _sync_get(self, vm_id: str) -> VMRecord:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM vms WHERE id=?", (vm_id,)).fetchone()
        if row is None:
            raise VMNotFoundError(vm_id)
        record = _record_from_row(row)
        return self._resolve_build(record)

    def _sync_delete(self, vm_id: str) -> None:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM vms WHERE id=?", (vm_id,))
            conn.commit()
        if cursor.rowcount == 0:
            raise VMNotFoundError(vm_id)

    def _sync_update_status(self, vm_id: str, new_status: VMStatus) -> VMRecord:
        record = self._sync_get(vm_id)
        record.status = new_status
        record.updated_at = _utcnow()
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE vms SET status=?, data=?, updated_at=? WHERE id=?",
                (new_status, record.model_dump_json(), record.updated_at.isoformat(), vm_id),
            )
            conn.commit()
        return record

    # ── VMRepository interface (async wrappers) ───────────────────────────────

    async def create_vm(self, request: CreateVMRequest) -> VMRecord:
        import uuid
        record = VMRecord(
            id=str(uuid.uuid4()),
            name=request.name,
            image_id=request.image_id,
            flavor_id=request.flavor_id,
            network_id=request.network_id,
            status=VMStatus.BUILD,
        )
        await asyncio.to_thread(self._sync_create, record)
        logger.info("vm_created", extra={"vm_id": record.id, "vm_name": record.name, "backend": "sqlite"})
        return record

    async def get_vm(self, vm_id: str) -> VMRecord:
        record = await asyncio.to_thread(self._sync_get, vm_id)
        logger.debug("vm_fetched", extra={"vm_id": vm_id, "status": record.status, "backend": "sqlite"})
        return record

    async def delete_vm(self, vm_id: str) -> None:
        await asyncio.to_thread(self._sync_delete, vm_id)
        logger.info("vm_deleted", extra={"vm_id": vm_id, "backend": "sqlite"})

    async def start_vm(self, vm_id: str) -> VMRecord:
        record = await asyncio.to_thread(self._sync_get, vm_id)
        if record.status != VMStatus.STOPPED:
            raise VMOperationError(
                vm_id, "start",
                f"VM is in '{record.status}' state — only STOPPED VMs can be started",
            )
        updated = await asyncio.to_thread(self._sync_update_status, vm_id, VMStatus.ACTIVE)
        logger.info("vm_started", extra={"vm_id": vm_id, "backend": "sqlite"})
        return updated

    async def stop_vm(self, vm_id: str) -> VMRecord:
        record = await asyncio.to_thread(self._sync_get, vm_id)
        if record.status != VMStatus.ACTIVE:
            raise VMOperationError(
                vm_id, "stop",
                f"VM is in '{record.status}' state — only ACTIVE VMs can be stopped",
            )
        updated = await asyncio.to_thread(self._sync_update_status, vm_id, VMStatus.STOPPED)
        logger.info("vm_stopped", extra={"vm_id": vm_id, "backend": "sqlite"})
        return updated
