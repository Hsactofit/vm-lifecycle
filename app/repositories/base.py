"""Repository interface (Abstract Base Class).

Defines the contract that both MockVMRepository and OpenStackVMRepository
must fulfil. The service layer depends only on this interface — it never
imports a concrete implementation directly, which keeps the two sides of
the dependency boundary truly decoupled.

Design notes
────────────
* All methods are async so the interface is compatible with both:
    - async I/O (real OpenStack HTTP calls via openstacksdk)
    - synchronous in-memory operations wrapped in coroutines (mock)
* Exceptions raised by implementations:
    - VMNotFoundError  – resource does not exist
    - VMOperationError – action is invalid for current state
    - OpenStackError   – backend-specific failure (real impl only)
"""
from abc import ABC, abstractmethod

from app.models.requests import CreateVMRequest
from app.models.vm import VMRecord


class VMRepository(ABC):
    @abstractmethod
    async def create_vm(self, request: CreateVMRequest) -> VMRecord:
        """Initiate VM creation. Returns a record in BUILD state."""
        ...

    @abstractmethod
    async def get_vm(self, vm_id: str) -> VMRecord:
        """Return the current state of a VM.

        Raises:
            VMNotFoundError: if vm_id does not exist.
        """
        ...

    @abstractmethod
    async def delete_vm(self, vm_id: str) -> None:
        """Permanently remove a VM.

        Raises:
            VMNotFoundError: if vm_id does not exist.
        """
        ...

    @abstractmethod
    async def start_vm(self, vm_id: str) -> VMRecord:
        """Start a VM that is in STOPPED state.

        Raises:
            VMNotFoundError:  vm_id does not exist.
            VMOperationError: VM is not in STOPPED state.
        """
        ...

    @abstractmethod
    async def stop_vm(self, vm_id: str) -> VMRecord:
        """Stop a VM that is in ACTIVE state.

        Raises:
            VMNotFoundError:  vm_id does not exist.
            VMOperationError: VM is not in ACTIVE state.
        """
        ...
