# VM Lifecycle API

Production-style REST API for managing OpenStack VM lifecycle operations. Demonstrates clean architecture, testability, and real OpenStack integration without requiring a live cloud environment.

| | |
|---|---|
| **Stack** | Python 3.11 · FastAPI · Pydantic v2 · pytest |
| **Tests** | 57 passing · 97% coverage |
| **Backends** | `mock` (in-memory) · `sqlite` (persistent) · `openstack` (real cloud) |
| **Tested on** | DevStack (local all-in-one — Nova, Neutron, Cinder, Keystone) |

---

## Quick Start

```bash
git clone <repo-url> && cd vm-lifecycle-api

pip install -r requirements-dev.txt
cp .env.example .env

uvicorn app.main:app --reload
# → http://localhost:8000/docs
```

---

## Architecture

```
app/
├── api/          routes · middleware · dependency injection
├── core/         config · exceptions · structured logging
├── models/       request / response schemas (Pydantic)
├── repositories/ VMRepository ABC → Mock · SQLite · OpenStack
├── services/     VMService — business logic only
└── main.py
```

**Request flow:**

```
Request → RequestTracingMiddleware → FastAPI Router → VMService → VMRepository
                                                                      ├── MockVMRepository      (BACKEND=mock)
                                                                      ├── SQLiteVMRepository    (BACKEND=sqlite)
                                                                      └── OpenStackVMRepository (BACKEND=openstack)
```

→ Full details: [docs/architecture.md](docs/architecture.md)

---

## API Endpoints

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| `POST` | `/api/v1/vms` | Create VM — returns `BUILD` immediately | 201 |
| `GET` | `/api/v1/vms/{id}` | Get VM details and current status | 200 |
| `DELETE` | `/api/v1/vms/{id}` | Delete VM | 204 |
| `POST` | `/api/v1/vms/{id}/start` | Start a stopped VM | 200 |
| `POST` | `/api/v1/vms/{id}/stop` | Stop a running VM | 200 |
| `GET` | `/health` | Liveness probe | 200 |
| `GET` | `/health/ready` | Readiness probe — validates backend | 200/503 |

VM status flow: `BUILD → ACTIVE → STOPPED ⇄ ACTIVE`

`BUILD → ACTIVE` is asynchronous — poll `GET /vms/{id}` to observe the transition, mirroring how real OpenStack Nova clients work.

→ Full examples and error codes: [docs/api-reference.md](docs/api-reference.md)

---

## Design Decisions

**Repository Pattern** — `VMRepository` is an abstract base class. `VMService` imports only the interface. Switching from mock to OpenStack or CloudStack is one config change; no existing code changes.

**Lazy BUILD → ACTIVE** — status transitions are computed on read (elapsed time since `created_at`), not via background tasks. Race-condition free, deterministic in tests, and matches how clients actually poll Nova.

**Three-tier error mapping** — `VMNotFoundError` → 404, `VMOperationError` → 422, `OpenStackError` → 502. OpenStack SDK errors are caught in the repository and re-raised as domain types; the service layer never sees SDK-specific exceptions.

**Structured logging** — every log record includes `vm_id` and `request_id` as top-level JSON fields. Log aggregators (Loki, Datadog, CloudWatch) can index them without regex.

**Non-blocking I/O** — all OpenStack SDK calls (synchronous by nature) are wrapped in `asyncio.to_thread()` so one slow Nova call cannot stall unrelated requests on the event loop.

**Singleton connections** — the OpenStack repository is constructed once via `@cache`. Keystone auth happens at startup, not per-request.

---

## Trade-offs

| | Mock | SQLite | OpenStack |
|---|---|---|---|
| Setup | Zero | Zero | Credentials + DevStack |
| Persistence | Lost on restart | Survives restarts | Durable in Nova DB |
| Multi-node | No | No | Yes |
| CI suitability | Perfect | Good | Needs OpenStack in CI |
| Production | No | Single-node only | Yes |

---

## Running Tests

```bash
pytest tests/ -v                              # all 57 tests
pytest tests/ --cov=app --cov-report=term-missing  # with coverage
pytest tests/unit/ -v                         # service + repository layer
pytest tests/integration/ -v                  # full HTTP cycle
```

Tests never touch a real OpenStack environment. All backends are swapped via `app.dependency_overrides`. The full suite runs in under 0.3 seconds.

---

## OpenStack Integration

Validated against a local **DevStack** instance running Nova, Neutron, Cinder, Keystone, and Glance. Switch to the real backend with:

```bash
BACKEND=openstack uvicorn app.main:app --reload
```

Requires `openstacksdk`: `pip install openstacksdk`

| Service | Role |
|---|---|
| **Keystone** | Identity and token auth — SDK handles this transparently |
| **Nova** | All VM lifecycle calls map 1-to-1 to Nova compute API |
| **Neutron** | Network port allocated automatically on VM create |
| **Glance** | Boot image referenced by UUID in create request |
| **Cinder** | Volume management — roadmap item |

→ Full setup walkthrough, service details, and lifecycle test steps: [docs/openstack-testing.md](docs/openstack-testing.md)

---

## Configuration

```bash
cp .env.example .env   # all variables documented inline
```

Key variables: `BACKEND` · `LOG_LEVEL` · `LOG_FORMAT` · `LOG_FILE` · `SQLITE_DB_PATH` · `OS_AUTH_URL` · `OS_USERNAME` · `OS_PASSWORD`

---

## Docker

```bash
docker compose up --build
# API available at http://localhost:8000
```

Multi-stage Dockerfile — packages installed to `/usr/local` (world-readable), runs as non-root `appuser`.

---

## Future Roadmap

| Priority | Item |
|----------|------|
| P0 | PostgreSQL backend (SQLAlchemy async + asyncpg) |
| P0 | Authentication — JWT / Keystone token middleware |
| P1 | `GET /vms` — list all VMs with pagination |
| P1 | Background status sync — Celery worker polls Nova |
| P1 | Retry with exponential backoff (tenacity) |
| P2 | Cinder volume management endpoints |
| P2 | Neutron port management (floating IPs) |
| P3 | OpenTelemetry distributed tracing |
| P3 | Multi-tenancy — per-project isolation via JWT |

---

## Docs

| Document | Contents |
|----------|----------|
| [docs/architecture.md](docs/architecture.md) | Layer diagram · design decisions · OpenStack service mapping |
| [docs/api-reference.md](docs/api-reference.md) | Full endpoint reference · curl examples · error codes |
| [docs/openstack-testing.md](docs/openstack-testing.md) | DevStack setup · CloudStack notes · MicroStack |
