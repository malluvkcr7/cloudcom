# Distributed Key-Value Store (MVP)

A minimal distributed key-value store prototype implemented in Python using FastAPI with full replication, persistence, and failure recovery.

## Architecture

**Core Components:**
- **Controller** — maintains worker registry, answers partition mapping queries (primary + replicas), watches heartbeats and triggers re-replication when workers fail
- **Workers** — store key/value pairs, persist them to Docker volumes, accept replication requests and heartbeat to the controller. Writes use configurable `WRITE_QUORUM`

**Key Features:**
- ✅ Partitioning via `hash(key) mod N` → primary + replica selection
- ✅ Replication with configurable `REPLICAS=3` and `WRITE_QUORUM=2`
- ✅ Controller watcher detects failed workers (heartbeat timeout) and triggers re-replication
- ✅ Worker persistence: each key stored as JSON file under `/app/data` on Docker volumes (survives restarts)
- ✅ Dynamic worker registration via heartbeats (no pre-configuration needed)
- ✅ Integration tests (pytest) covering mapping, quorum, and re-replication behavior
- ✅ Interactive web UI for demonstrating operations
- ✅ Docker Compose stack with controller + 4 workers, health checks, and named volumes

---

## Quick Start (Local Development)

### Prerequisites
- Python 3.10+
- Docker and Docker Compose (for containerized demo)

### 1. Install Dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Tests

```bash
.venv/bin/python -m pytest -q
```

All 3 integration tests should pass (controller mapping, quorum writes, re-replication).

### 3. Manual Run (Two-Terminal Workflow)

**Terminal A — Controller:**
```bash
export PYTHONPATH=.
uvicorn controller:app --reload --host 0.0.0.0 --port 8000
```

**Terminal B — Worker:**
```bash
export CONTROLLER=http://localhost:8000
export ADDRESS=http://localhost:8001
export ID=w1
export PYTHONPATH=.
uvicorn worker:app --reload --host 0.0.0.0 --port 8001
```

Test with:
```bash
curl -X PUT "http://localhost:8001/kv/mykey" -H 'Content-Type: application/json' -d '{"value":"hello"}'
curl http://localhost:8001/kv/mykey
```

---

## Docker Compose Demo (Recommended for Viva)

### Start the Stack

```bash
# If Docker requires sudo
sudo docker compose up -d --build

# Or if user has Docker socket access
docker compose up -d --build
```

This starts:
- 1 Controller (port 8000)
- 4 Workers (ports 8001–8004)
- Named volumes for persistence on each worker

### Access the Web UI

**Open your browser:** http://localhost:8000/

The interactive UI provides:
- **📝 Write Key** — Store a value with quorum replication
- **📖 Read Key** — Retrieve from any worker
- **📊 System Status** — Monitor worker health (auto-refreshes every 10s)
- **📋 List Keys** — Browse all stored keys

### Health Check

```bash
curl http://localhost:8000/health
```

Expected output:
```json
{"status":"controller up","workers_count":4}
```

### Useful Debug Commands

```bash
# View all services
docker compose ps

# Watch logs
docker compose logs -f controller
docker compose logs -f worker1

# Inspect a single service
curl http://localhost:8001/health

# Clean up (removes volumes)
docker compose down -v
```

---

## Viva Demonstration Script

### 1. Start the Stack

```bash
sudo docker compose up -d --build
sleep 3
```

### 2. Show System Health

```bash
curl http://localhost:8000/health
```
*Expected: 4 workers registered*

### 3. Get Partition Mapping

```bash
curl "http://localhost:8000/map?key=demo-key"
```
*Shows primary worker + 2 replicas for the key*

### 4. Write Using the Web UI (Recommended)

Open http://localhost:8000/ and use the **Write Key** section:
- Key: `demo-key`
- Value: `demo-value`
- Click **Write**

Or via CLI:
```bash
curl -s -X PUT "http://localhost:8001/kv/demo-key" \
  -H 'Content-Type: application/json' \
  -d '{"value":"demo-value"}'
```

### 5. Verify Replication (Read from Multiple Workers)

Using the Web UI:
- Use the **Read Key** section and try reading from different workers (dropdown menu)

Or via CLI:
```bash
curl http://localhost:8001/kv/demo-key
curl http://localhost:8002/kv/demo-key
curl http://localhost:8003/kv/demo-key
```

*All three should return the value (demonstrates replication)*

### 6. Demonstrate Failure + Re-replication

**In Web UI or Terminal 1:**
```bash
docker compose stop worker2
```

**In Terminal 2 (watch controller logs):**
```bash
docker compose logs -f controller
```

*You'll see:*
- Controller detects worker2 heartbeat timeout (after ~6s)
- Controller marks worker2 as down
- Controller instructs another worker to pull missing keys from backups
- Replicas are automatically restored

**Recover the worker:**
```bash
docker compose start worker2
```

*Worker2 rejoins and re-synchronizes with the cluster*

---

## Data Persistence

Each worker stores data in a Docker named volume mounted at `/app/data`:

**Local filesystem location:**
```bash
sudo ls /var/lib/docker/volumes/kvstore_worker1_data/_data/
```

**Key format:** JSON files named after the key (URL-encoded):
```
/var/lib/docker/volumes/kvstore_worker1_data/_data/mykey
```

**Contents:**
```json
{"value": "hello"}
```

**Persistence across restarts:**
```bash
docker compose stop worker1
docker compose start worker1
# Data is still there!
curl http://localhost:8001/kv/mykey
```

---

## API Reference

### Controller Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check; returns worker count |
| GET | `/map?key=<key>` | Get primary + replica addresses for key |
| GET | `/workers` | List all registered workers |
| POST | `/heartbeat` | Worker heartbeat (internal) |

### Worker Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| PUT | `/kv/{key}` | Write key (coordinates replication) |
| GET | `/kv/{key}` | Read key value |
| GET | `/keys` | List all keys stored locally |
| POST | `/replicate/{key}` | Receive replicated write (internal) |
| POST | `/pull` | Pull keys from peer (internal) |
| GET | `/health` | Health check |

---

## Configuration

**Controller Environment Variables:**
```bash
WORKERS              # Comma-separated worker URLs (used for initial startup)
REPLICAS            # Number of replicas per key (default: 3)
HEARTBEAT_TIMEOUT   # Seconds before marking worker down (default: 6)
CHECK_INTERVAL      # Seconds between failure checks (default: 2)
```

**Worker Environment Variables:**
```bash
CONTROLLER          # Controller URL
ADDRESS             # Worker's own address
ID                  # Worker ID (e.g., w1)
WRITE_QUORUM        # Acks needed for write success (default: 2)
DATA_DIR            # Directory for persistence (default: /app/data in containers)
REQUEST_TIMEOUT     # HTTP timeout in seconds (default: 2)
```

---

## Testing

### Run All Tests

```bash
.venv/bin/python -m pytest -q
```

### Test Coverage

1. **test_controller.py** — Verifies mapping returns correct primary + replicas
2. **test_quorum.py** — Tests write quorum behavior when workers fail
3. **test_rereplication.py** — Validates controller detects failures and re-replicates

All tests pass with robust retry logic (allows time for heartbeat propagation).

---

## Troubleshooting

### Port Already in Use

```bash
# Kill conflicting processes
sudo lsof -i :8000
sudo kill -9 <PID>

# Or update docker-compose.yml ports
```

### Docker Permission Denied

```bash
# Option 1: Use sudo
sudo docker compose up -d --build

# Option 2: Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

### Worker Won't Register

Check heartbeat logs:
```bash
docker compose logs worker1 | grep heartbeat
```

Ensure `CONTROLLER` env var is correct (use service name for Docker: `http://controller:8000`)

### Data Not Persisting

Verify volume mount:
```bash
docker inspect kvstore_worker1_data
```

Check `/var/lib/docker/volumes/kvstore_worker1_data/_data/`

---

## Architecture Notes

- **Hash partitioning:** Uses SHA-256 hash to deterministically map keys to workers
- **Eventual consistency:** Reads may hit stale replicas during failure recovery
- **Quorum writes:** WRITE_QUORUM=2 means 1 local + 1 remote ack required
- **Failure detection:** Simple heartbeat with timeout; no fancy gossip protocol
- **Re-replication:** Sequential pull of missing keys; not optimized for large datasets

## Extension Ideas

- Async background replication (fire-and-forget for non-quorum replicas)
- Stronger failure detectors (phi-accrual)
- Consistent hashing ring for dynamic worker membership
- Load balancing across replicas
- Snapshot-based recovery instead of key-by-key replication
- Geo-distributed replicas

---

## License

MIT (educational use)



