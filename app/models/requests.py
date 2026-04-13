from pydantic import BaseModel, Field, field_validator


class CreateVMRequest(BaseModel):
    """Validated payload for VM creation.

    All four fields are required — there are no sensible defaults for image,
    flavor, or network because these are environment-specific identifiers.
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable VM name. Must be non-blank.",
    )
    image_id: str = Field(
        ...,
        min_length=1,
        description="ID or name of the boot image (e.g. 'ubuntu-22.04').",
    )
    flavor_id: str = Field(
        ...,
        min_length=1,
        description="ID or name of the flavor / instance type (e.g. 'm1.small').",
    )
    network_id: str = Field(
        ...,
        min_length=1,
        description="ID of the Neutron network to attach on boot.",
    )

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("VM name cannot be blank or whitespace-only")
        return stripped

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "web-server-01",
                "image_id": "ubuntu-22.04",
                "flavor_id": "m1.small",
                "network_id": "private-net-001",
            }
        }
    }
