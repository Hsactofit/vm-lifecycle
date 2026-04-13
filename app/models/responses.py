from datetime import datetime

from pydantic import BaseModel

from app.models.vm import VMStatus


class VMResponse(BaseModel):
    """Public representation of a VM returned by the API.

    Deliberately excludes internal fields (metadata) that are not yet
    part of the public contract.
    """

    id: str
    name: str
    image_id: str
    flavor_id: str
    network_id: str
    status: VMStatus
    created_at: datetime
    updated_at: datetime

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "name": "web-server-01",
                "image_id": "ubuntu-22.04",
                "flavor_id": "m1.small",
                "network_id": "private-net-001",
                "status": "BUILD",
                "created_at": "2024-06-01T12:00:00+00:00",
                "updated_at": "2024-06-01T12:00:00+00:00",
            }
        }
    }


class ActionResponse(BaseModel):
    """Returned after a VM lifecycle action (start / stop)."""

    vm_id: str
    action: str
    status: VMStatus
    message: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "vm_id": "550e8400-e29b-41d4-a716-446655440000",
                "action": "stop",
                "status": "STOPPED",
                "message": "VM stop initiated",
            }
        }
    }


class ErrorResponse(BaseModel):
    """Standardised error envelope returned by all exception handlers."""

    error: str
    detail: str
