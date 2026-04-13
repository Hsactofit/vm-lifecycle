"""Internal domain model.

VMRecord is the canonical in-memory representation used across all layers.
It is intentionally kept separate from API request/response models so that
internal state (e.g. created_at, metadata) never leaks into the wire format
unless explicitly serialised by the response schema.
"""
import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class VMStatus(str, Enum):
    """Mirrors OpenStack Nova server statuses where applicable.

    Reference: https://docs.openstack.org/api-guide/compute/server_concepts.html
    """

    BUILD = "BUILD"       # provisioning in progress
    ACTIVE = "ACTIVE"     # running
    STOPPED = "STOPPED"   # shut off (Nova: SHUTOFF)
    DELETED = "DELETED"   # soft-deleted / tombstone
    ERROR = "ERROR"       # unrecoverable failure


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VMRecord(BaseModel):
    """Immutable-ish domain object.  Repositories return new instances on state changes."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    image_id: str
    flavor_id: str
    network_id: str
    status: VMStatus = VMStatus.BUILD
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    metadata: dict = Field(default_factory=dict)

    model_config = {"frozen": False}  # mutable so repositories can update in place
