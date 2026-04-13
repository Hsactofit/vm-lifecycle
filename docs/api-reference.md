# API Reference

Base URL: `http://localhost:8000/api/v1`

Interactive docs: [`/docs`](http://localhost:8000/docs) (Swagger UI) · [`/redoc`](http://localhost:8000/redoc)

---

## Endpoints

| Method   | Path                  | Description                              | Success | Errors   |
|----------|-----------------------|------------------------------------------|---------|----------|
| `POST`   | `/vms`                | Create VM — returns `BUILD` immediately  | 201     | 422      |
| `GET`    | `/vms/{id}`           | Get VM and current status                | 200     | 404      |
| `DELETE` | `/vms/{id}`           | Delete VM (must not be in BUILD state)   | 204     | 404, 422 |
| `POST`   | `/vms/{id}/start`     | Start a STOPPED VM                       | 200     | 404, 422 |
| `POST`   | `/vms/{id}/stop`      | Stop an ACTIVE VM                        | 200     | 404, 422 |
| `GET`    | `/health`             | Liveness probe                           | 200     | —        |
| `GET`    | `/health/ready`       | Readiness probe (validates backend)      | 200     | 503      |

---

## VM Status lifecycle

```
POST /vms
    │
    ▼
 [BUILD]  ──── time (MOCK_BUILD_DELAY_SECONDS) ────►  [ACTIVE]
                                                          │    ▲
                                               POST /stop │    │ POST /start
                                                          ▼    │
                                                      [STOPPED]
```

`BUILD → ACTIVE` is async — poll `GET /vms/{id}` to observe the transition.

---

## Request / Response examples

### Create VM

```bash
curl -X POST http://localhost:8000/api/v1/vms \
  -H "Content-Type: application/json" \
  -d '{
    "name": "web-server-01",
    "image_id": "ubuntu-22.04",
    "flavor_id": "m1.small",
    "network_id": "private-net-001"
  }'
```

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "web-server-01",
  "status": "BUILD",
  "created_at": "2024-06-01T12:00:00+00:00",
  "updated_at": "2024-06-01T12:00:00+00:00"
}
```

### Get VM (after delay, status is ACTIVE)

```bash
curl http://localhost:8000/api/v1/vms/550e8400-e29b-41d4-a716-446655440000
```

```json
{"id": "550e8400...", "status": "ACTIVE", ...}
```

### Stop / Start

```bash
curl -X POST http://localhost:8000/api/v1/vms/550e8400.../stop
# {"vm_id": "...", "action": "stop", "status": "STOPPED", "message": "VM stop initiated successfully"}

curl -X POST http://localhost:8000/api/v1/vms/550e8400.../start
# {"vm_id": "...", "action": "start", "status": "ACTIVE", "message": "VM start initiated successfully"}
```

### Delete

```bash
curl -X DELETE http://localhost:8000/api/v1/vms/550e8400...
# 204 No Content

# Trying to delete a VM still in BUILD:
# 422 {"error": "VM_OPERATION_INVALID", "detail": "...", "operation": "delete"}
```

### Request tracing

```bash
curl -H "X-Request-ID: my-trace-id" http://localhost:8000/api/v1/vms/...
# Response header: X-Request-ID: my-trace-id
```

---

## Error codes

| HTTP | Error code                   | When                                       |
|------|------------------------------|--------------------------------------------|
| 404  | `VM_NOT_FOUND`               | VM ID does not exist                       |
| 409  | `VM_CONFLICT`                | Duplicate resource                         |
| 422  | `VM_OPERATION_INVALID`       | Invalid state transition                   |
| 422  | *(Pydantic detail)*          | Missing / invalid request body fields      |
| 502  | `OPENSTACK_ERROR`            | Backend failure                            |
| 502  | `OPENSTACK_QUOTA_EXCEEDED`   | Cloud quota exceeded                       |
| 502  | `OPENSTACK_BAD_REQUEST`      | Bad request to OpenStack API               |
| 503  | *(readiness)*                | Backend unreachable (OpenStack mode)       |
