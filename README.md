# Distributed Key-Value Store (MVP)

This small project implements a minimal distributed key-value store prototype with:
- 1 Controller (partition mapping + worker registry)
- Multiple Workers (in-memory key store + replication endpoints)

Quick start (local, requires Python 3.10+):

1. Create a virtualenv and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Start controller and a worker (manual start for local testing):

```bash
## Distributed Key-Value Store (MVP)

Small demonstrable prototype of a distributed key-value store implemented in Python using FastAPI.

Core components
- Controller — maintains worker registry, answers mapping queries (primary + replicas), watches heartbeats and triggers re-replication when workers fail.
- Workers — store key/value pairs, persist them to disk, accept replication requests and heartbeat to the controller. Writes use a configurable WRITE_QUORUM.

Key features implemented
- Partitioning via simple hash(key) → primary + replica selection.
- Replication with configurable REPLICAS and WRITE_QUORUM.
- Controller watcher that detects failed workers (heartbeat timeout) and triggers re-replication.
- Worker persistence: each key is stored as a file under the worker's data directory and loaded on startup.
- Integration tests (pytest) that exercise mapping, quorum and re-replication behavior.
- Dockerfile + docker-compose for demo (controller + 4 workers) and a helper script to smoke-test the stack.

Quick local setup (recommended for development)

1) Create virtualenv and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) Run tests (fast, uses local uvicorn processes)

```bash
".venv/bin/python" -m pytest -q
```

Manual run (two-terminal workflow)

Terminal A (controller):

```bash
uvicorn controller:app --reload --host 0.0.0.0 --port 8000
```

Terminal B (worker):

```bash
export CONTROLLER=http://localhost:8000
export ADDRESS=http://localhost:8001
export ID=w1
uvicorn worker:app --reload --host 0.0.0.0 --port 8001
```

Demo using Docker Compose (recommended for viva/demo)

1) Build and start the stack (from repo root)

```bash
# if your Docker requires sudo
sudo docker compose up -d --build
# or if your user can access docker socket
docker compose up -d --build
```



Useful debugging commands

```bash
docker compose ps
docker compose logs --tail=200 controller worker1 worker2 worker3 worker4
curl http://localhost:8000/health
curl http://localhost:8001/health
docker compose down -v
```

Demo scenario ideas for viva (commands)

- Show mapping for a key:
	curl "http://localhost:8000/map?key=somekey"

- Write a key (writes to primary and waits for WRITE_QUORUM):
	curl -X PUT "http://localhost:8001/kv/somekey" -H 'Content-Type: application/json' -d '{"value":"v"}'

- Show the key from each worker (to show replication):
	curl http://localhost:8001/kv/somekey
	curl http://localhost:8002/kv/somekey

- Demonstrate failure + re-replication:
	1. Stop a worker (example using compose):
		 docker compose stop worker2
	2. Wait a few seconds for controller watcher to detect the failure (HEARTBEAT_TIMEOUT configured in compose). Controller will log re-replication actions.
	3. Check that the controller instructed another live worker to pull missing keys and that replicas are restored.

Tips & notes
- If host ports 8000..8004 are in use, stop the conflicting processes (uvicorn instances used for local testing) before running compose, or change ports in `docker-compose.yml`.
- If docker reports "permission denied" access to the socket, either run compose with `sudo` or add your user to the `docker` group and re-login.
- The system is intentionally small; extension ideas include async background replication (fire-and-forget for non-quorum replicas), stronger failure detectors, and a consistent hashing ring for dynamic worker membership.



Viva 

1) Start the stack 
```bash
# if your Docker needs sudo
sudo docker compose up -d --build
```


2) Confirm services are healthy 

```bash
curl http://localhost:8000/health
```



3) Show mapping for a demo key 

```bash
curl "http://localhost:8000/map?key=demo-key"
```



4) Put a key 

```bash
curl -s -X PUT "http://localhost:8001/kv/demo-key" -H 'Content-Type: application/json' -d '{"value":"v1"}'
```



5) Read the key from multiple workers 

```bash
curl http://localhost:8001/kv/demo-key
curl http://localhost:8002/kv/demo-key
curl http://localhost:8003/kv/demo-key
```



6) Demonstrate failure + re-replication 

```bash
docker compose stop worker2
# watch controller logs for re-replication activity
docker compose logs -f controller
```



