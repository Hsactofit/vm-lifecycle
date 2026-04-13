# Architecture & Design

## Layered Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      HTTP Client                          │
└──────────────────────┬───────────────────────────────────┘
                       │  HTTP (JSON)
┌──────────────────────▼───────────────────────────────────┐
│                   API Layer (FastAPI)                      │
│  app/api/routes/vms.py  ·  app/api/routes/health.py       │
│  app/api/middleware.py  ·  app/api/deps.py                 │
│                                                            │
│  • Input validation (Pydantic)                             │
│  • HTTP status codes & error serialisation                 │
│  • Request tracing (X-Request-ID header)                   │
│  • Dependency injection wiring                             │
└──────────────────────┬───────────────────────────────────┘
                       │  domain objects (VMRecord)
┌──────────────────────▼───────────────────────────────────┐
│                 Service Layer (VMService)                   │
│  app/services/vm_service.py                                │
│                                                            │
│  • Business rule enforcement                               │
│    e.g. "cannot delete a BUILD-state VM"                   │
│  • Structured logging with vm_id + request_id              │
│  • Orchestrates repository calls                           │
│  • Backend-agnostic (no import of any repository)          │
└──────────────────────┬───────────────────────────────────┘
                       │  VMRepository (ABC)
           ┌───────────┴────────────┐
           │                        │
┌──────────▼──────────┐  ┌──────────▼──────────────────────┐
│  MockVMRepository   │  │  OpenStackVMRepository            │
│  (default)          │  │  (BACKEND=openstack)              │
│                     │  │                                    │
│  in-memory dict     │  │  openstacksdk                     │
│  lazy BUILD→ACTIVE  │  │  Nova  · Neutron · Cinder         │
│  zero dependencies  │  │  error mapping · retry-ready      │
└─────────────────────┘  └──────────────────────────────────┘
```

## Key Design Decisions

### 1. Repository Pattern

The `VMRepository` ABC is the central design decision. It means:

- The service layer has **zero knowledge** of how VMs are stored. It only
  calls `create_vm`, `get_vm`, etc. and trusts the contract.
- Switching from mock to OpenStack requires **one config change** (`BACKEND=openstack`),
  not code changes.
- Tests can inject a `MockVMRepository` with controlled delays, making
  state-transition tests deterministic and milliseconds-fast.

### 2. Lazy Status Resolution (BUILD → ACTIVE)

Rather than using background threads or asyncio tasks to update status,
the mock uses **lazy resolution**: when `get_vm` is called, it computes
whether enough time has elapsed to transition BUILD → ACTIVE. This is:

- **Race-condition free**: no concurrent mutation of shared state
- **Deterministic in tests**: set `build_delay_seconds=0` to skip the delay
- **Semantically correct**: matches how OpenStack clients actually work —
  they poll `GET /servers/{id}` rather than receiving push notifications

### 3. Structured Logging

Every log statement emits a JSON object (in production) with:
- `timestamp`, `level`, `logger`, `message` — standard fields
- `vm_id`, `request_id` — domain context promoted to top-level keys

This enables log aggregators (Loki, Datadog, CloudWatch) to query
`vm_id="abc-123"` across all operations without regex parsing.

### 4. Error Taxonomy

```
VMError (base)
├── VMNotFoundError     → HTTP 404
├── VMConflictError     → HTTP 409
├── VMOperationError    → HTTP 422  (invalid state transition)
└── OpenStackError      → HTTP 502  (backend failure)
```

Errors from openstacksdk (ResourceNotFound, OverLimit, Conflict…) are
caught in the repository and re-raised as domain exceptions. The service
layer and routes never import OpenStack-specific error types, keeping
the dependency boundary intact.

### 5. Dependency Injection

FastAPI's `Depends()` system is used for:

| Dependency       | Resolved at       | Override in tests via           |
|------------------|-------------------|---------------------------------|
| `get_settings`   | request time      | environment / `.env` file       |
| `get_repository` | request time      | `app.dependency_overrides`      |
| `get_vm_service` | request time      | follows `get_repository`        |
| `get_request_id` | middleware → state | injected by `RequestTracingMiddleware` |

## OpenStack Integration Design

When `BACKEND=openstack`, the `OpenStackVMRepository` maps to three Nova/Neutron/Cinder services:

| Feature         | OpenStack Service | API Call                          |
|-----------------|-------------------|-----------------------------------|
| Create VM       | Nova (Compute)    | `compute.create_server`           |
| Get VM status   | Nova              | `compute.get_server`              |
| Delete VM       | Nova              | `compute.delete_server`           |
| Start VM        | Nova              | `compute.start_server`            |
| Stop VM         | Nova              | `compute.stop_server`             |
| Network attach  | Neutron           | via `networks=[{"uuid": ...}]`    |
| Volume attach   | Cinder *(future)* | `compute.create_volume_attachment`|

### Nova Status Mapping

| Nova status | Our VMStatus |
|-------------|-------------|
| BUILD       | BUILD       |
| ACTIVE      | ACTIVE      |
| SHUTOFF     | STOPPED     |
| PAUSED      | STOPPED     |
| SUSPENDED   | STOPPED     |
| DELETED     | DELETED     |
| ERROR       | ERROR       |

## Future Architecture Directions

### Short-term (next sprint)

- **Persistent storage**: replace the in-memory dict with PostgreSQL
  (SQLAlchemy async) so state survives restarts
- **Background task queue**: Celery + Redis to poll OpenStack status
  asynchronously and push events to clients via SSE or webhooks
- **Retry logic**: tenacity with exponential backoff on transient
  OpenStack 503s

### Medium-term

- **API versioning**: `/api/v2/vms` with additive changes; v1 remains stable
- **Multi-tenancy**: per-project repositories keyed on JWT sub claim
- **Neutron port management**: dedicated endpoint for attaching/detaching
  network interfaces post-creation
- **Cinder volume lifecycle**: attach, detach, snapshot endpoints

### Long-term

- **Event sourcing**: append-only VM event log for audit trail and
  time-travel debugging
- **OpenTelemetry**: traces across Nova/Neutron/Cinder calls with
  span propagation
