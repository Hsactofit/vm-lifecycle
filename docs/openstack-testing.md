# Testing with DevStack

This service was validated against a local **DevStack** all-in-one installation.
DevStack installs the full OpenStack stack — Nova, Neutron, Cinder, Keystone, and Glance —
on a single machine, making it the standard environment for development and integration testing
without a public cloud account.

---

## OpenStack Services Used

### Keystone — Identity & Auth
Every API call starts here. Keystone issues tokens that Nova, Neutron, and Cinder
all validate before serving a request. `openstacksdk` handles token negotiation
transparently — you provide credentials once in `clouds.yaml` or `.env`, and the SDK
refreshes tokens as needed.

In our service: the singleton `OpenStackVMRepository` authenticates once at startup
via `openstack.connect()`. All subsequent calls reuse the session.

### Nova — Compute (VM lifecycle)
Nova is the core of our implementation. It manages the hypervisor and schedules
VMs across compute nodes.

| Our API call | Nova API call |
|---|---|
| `POST /vms` | `compute.create_server()` |
| `GET /vms/{id}` | `compute.get_server()` |
| `DELETE /vms/{id}` | `compute.delete_server()` |
| `POST /vms/{id}/start` | `compute.start_server()` |
| `POST /vms/{id}/stop` | `compute.stop_server()` |

Nova returns `BUILD` immediately on create and transitions to `ACTIVE` asynchronously —
this is the real-world behaviour our mock replicates exactly.

Nova status values and how we map them:

| Nova status | Our `VMStatus` |
|---|---|
| `BUILD` | `BUILD` |
| `ACTIVE` | `ACTIVE` |
| `SHUTOFF` | `STOPPED` |
| `PAUSED` | `STOPPED` |
| `ERROR` | `ERROR` |

### Neutron — Networking
When you create a VM, Nova calls Neutron to allocate a port on the specified network
and attach it to the instance. This happens automatically when you pass
`networks=[{"uuid": network_id}]` to `create_server`.

In our `OpenStackVMRepository.create_vm`:
```python
self._conn.compute.create_server(
    name=request.name,
    image_id=request.image_id,
    flavor_id=request.flavor_id,
    networks=[{"uuid": request.network_id}],  # ← Neutron port allocated here
)
```

DevStack creates two networks by default: `private` (tenant network) and `public`
(for floating IPs). We used the `private` network for testing.

Future work: dedicated `POST /vms/{id}/ports` and `POST /vms/{id}/floating-ip`
endpoints using `conn.network.create_port()` and `conn.network.create_ip()`.

### Glance — Image Service
Nova fetches the boot image from Glance during provisioning. You reference an image
by its UUID when calling `POST /vms`. DevStack pre-loads a CirrOS image (a tiny
test Linux) which is what we used during validation.

```bash
openstack image list
# +--------------------------------------+--------------------------+--------+
# | ID                                   | Name                     | Status |
# +--------------------------------------+--------------------------+--------+
# | abc123...                            | cirros-0.6.2-x86_64-disk | active |
```

### Cinder — Block Storage
Cinder manages persistent volumes. Our current implementation uses only Nova-attached
ephemeral storage (the VM's root disk). Cinder integration is on the roadmap:

```
POST /vms/{id}/volumes         → conn.compute.create_volume_attachment()
DELETE /vms/{id}/volumes/{vid} → conn.compute.delete_volume_attachment()
```

Cinder was enabled in our DevStack config and confirmed reachable — volume attachment
is the next planned endpoint.

---

## DevStack Setup

### Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| OS | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| CPU | 4 vCPUs | 8 vCPUs |
| RAM | 8 GB | 16 GB |
| Disk | 50 GB | 80 GB |

On macOS, use [Multipass](https://multipass.run) to run the Ubuntu VM:

```bash
brew install --cask multipass
multipass launch 22.04 --name devstack --cpus 4 --memory 8G --disk 60G
multipass shell devstack
```

### Install

```bash
# Inside the Ubuntu VM
sudo useradd -s /bin/bash -d /opt/stack -m stack
echo "stack ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/stack
sudo -u stack -i

git clone https://opendev.org/openstack/devstack /opt/stack/devstack
cd /opt/stack/devstack

cat > local.conf << 'EOF'
[[local|localrc]]
ADMIN_PASSWORD=secret
DATABASE_PASSWORD=secret
RABBIT_PASSWORD=secret
SERVICE_PASSWORD=secret

enable_service n-cpu n-api n-cond n-sch   # Nova    — VM lifecycle
enable_service neutron q-svc q-agt        # Neutron — network attachment
enable_service cinder c-api c-vol c-sch   # Cinder  — block storage
EOF

./stack.sh    # 20–40 minutes
```

When complete you'll see:
```
Horizon is now available at http://10.x.x.x/dashboard
Keystone is serving at http://10.x.x.x/identity/
```

---

## Connecting This Service to DevStack

```bash
# Get the VM IP
multipass info devstack | grep IPv4    # e.g. 192.168.64.5
```

Update your `.env`:

```bash
BACKEND=openstack
OS_AUTH_URL=http://192.168.64.5/identity/v3
OS_USERNAME=admin
OS_PASSWORD=secret
OS_PROJECT_NAME=admin
OS_USER_DOMAIN_NAME=Default
OS_PROJECT_DOMAIN_NAME=Default
OS_CLOUD=devstack
```

Install the SDK and run:

```bash
pip install openstacksdk
uvicorn app.main:app --reload
```

Check readiness (validates Nova connectivity):
```bash
curl http://localhost:8000/health/ready
# {"status": "ready", "backend": "openstack", "detail": "nova reachable"}
```

---

## Running the Full Lifecycle Against DevStack

### 1. Get real resource IDs

```bash
# Inside the DevStack VM
source /opt/stack/devstack/openrc admin admin

openstack image list   # cirros is pre-loaded
openstack flavor list  # m1.tiny, m1.small, etc.
openstack network list # use "private"
```

### 2. Create a VM

```bash
curl -X POST http://localhost:8000/api/v1/vms \
  -H "Content-Type: application/json" \
  -d '{
    "name": "devstack-test-vm",
    "image_id": "<cirros image uuid>",
    "flavor_id": "<m1.tiny uuid>",
    "network_id": "<private network uuid>"
  }'
# Returns: {"status": "BUILD", "id": "..."}
```

Nova creates the server record immediately and begins scheduling.

### 3. Poll until ACTIVE

```bash
VM_ID="<id from step 2>"
curl http://localhost:8000/api/v1/vms/$VM_ID
# After 30–90 seconds: {"status": "ACTIVE", ...}
```

Confirm in DevStack dashboard: `http://192.168.64.5/dashboard` → Compute → Instances.

### 4. Stop and start

```bash
curl -X POST http://localhost:8000/api/v1/vms/$VM_ID/stop
# Nova transitions: ACTIVE → SHUTOFF (we expose as STOPPED)

curl -X POST http://localhost:8000/api/v1/vms/$VM_ID/start
# Nova transitions: SHUTOFF → ACTIVE
```

### 5. Delete

```bash
curl -X DELETE http://localhost:8000/api/v1/vms/$VM_ID
# 204 No Content — Nova terminates the instance and Neutron releases the port
```

---

## Teardown

```bash
multipass delete devstack && multipass purge
```
