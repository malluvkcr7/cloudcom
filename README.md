# Distributed Key-Value Store

A distributed key-value store with replication, persistence, and failure recovery.

## What It Does

- **Hash-based partitioning:** Maps keys to 4 workers using `hash(key) mod 4`
- **3-replica replication:** Each key stored on primary + 2 replicas
- **Quorum writes:** Write succeeds after 2/3 replicas acknowledge (configurable)
- **Persistence:** Data persists on Docker volumes, survives restarts
- **Failure detection:** Controller detects failed workers via heartbeats and triggers re-replication
- **Interactive web UI:** Visual interface to write, read, and monitor the system

## Quick Start

### Prerequisites
- Python 3.10+
- Docker and Docker Compose

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

This runs 3 integration tests covering mapping, quorum writes, and failure recovery.

### 3. Run with Docker Compose

```bash
sudo docker compose up -d --build
```

This starts 1 controller + 4 workers with persistent Docker volumes.

## Testing All Features

### 1. Check System Health

```bash
curl http://localhost:8000/health
```

Expected: `{"status":"controller up","workers_count":4}`

### 2. Get Partition Mapping

```bash
curl "http://localhost:8000/map?key=mykey"
```

Shows which workers hold the key and its replicas.

### 3. Write a Key (with Replication)

```bash
curl -X PUT "http://localhost:8001/kv/mykey" \
  -H 'Content-Type: application/json' \
  -d '{"value":"myvalue"}'
```

### 4. Read from Different Workers

```bash
curl http://localhost:8001/kv/mykey
curl http://localhost:8002/kv/mykey
curl http://localhost:8003/kv/mykey
```

All should return the same value (confirms replication).

### 5. Verify Data Persists in Volume

```bash
sudo ls /var/lib/docker/volumes/kvstore_worker1_data/_data/
sudo cat /var/lib/docker/volumes/kvstore_worker1_data/_data/mykey
```

## Check Persistence Across Container Restart

You can verify that data survives container restarts using this sequence:

```bash
# 1. Write a key
curl -X PUT "http://localhost:8001/kv/persist-test" \
  -H 'Content-Type: application/json' \
  -d '{"value":"this-should-persist"}'

# 2. Verify it's in the Docker volume
sudo cat /var/lib/docker/volumes/kvstore_worker1_data/_data/persist-test

# 3. Restart the container
sudo docker compose restart worker1
sleep 2

# 4. Read the key after restart
curl http://localhost:8001/kv/persist-test

# Expected output:
# {"value":"this-should-persist"}
```

If you see the same value after restart, persistence is working!

### 6. Test Failure Detection + Re-replication

**Stop a worker:**
```bash
docker compose stop worker2
```

**Watch controller logs (in another terminal):**
```bash
docker compose logs -f controller
```

You'll see the controller detect the failure and trigger re-replication.

**Restart the worker:**
```bash
docker compose start worker2
```

### 7. Use the Web UI

Open http://localhost:8000/ in your browser for interactive testing:
- Write keys
- Read keys from specific workers
- View system status
- List all stored keys

## Useful Commands

```bash
# View all services
docker compose ps

# View logs
docker compose logs -f controller
docker compose logs -f worker1

# Clean up
docker compose down -v
```



