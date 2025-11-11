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

2) Quick smoke-check helper (already added):

```bash
chmod +x ./scripts/check_compose.sh
./scripts/check_compose.sh
```

What the smoke script does
- Builds & starts the compose stack
- Waits for controller + workers to report healthy
- Performs a simple PUT -> GET against `worker1` to confirm quorum replication

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



Viva script (short, read-aloud steps)
-----------------------------------
Use this small script during the viva — run the commands and say the short explanation next to each step.

1) Start the stack (explain: starts controller + 4 workers in containers)

```bash
# if your Docker needs sudo
sudo docker compose up -d --build
```

Say: "I start the controller and four workers using docker-compose. The controller listens on port 8000 and each worker on 8001..8004."

2) Confirm services are healthy (explain: controller sees 4 workers)

```bash
curl http://localhost:8000/health
```

Say: "Controller returned its health and reports four registered workers." (expect JSON with workers_count:4)

3) Show mapping for a demo key (explain: controller maps key -> primary + replicas)

```bash
curl "http://localhost:8000/map?key=demo-key"
```

Say: "This shows how the controller maps keys to a primary and replicas — the first address is the primary."

4) Put a key (explain: write waits for a write-quorum)

```bash
curl -s -X PUT "http://localhost:8001/kv/demo-key" -H 'Content-Type: application/json' -d '{"value":"v1"}'
```

Say: "The worker storing the primary attempts to replicate to the configured replicas and returns once the write-quorum is met." (expect acks >= WRITE_QUORUM)

5) Read the key from multiple workers (explain: show replication)

```bash
curl http://localhost:8001/kv/demo-key
curl http://localhost:8002/kv/demo-key
curl http://localhost:8003/kv/demo-key
```

Say: "You can see the key is present on multiple workers due to replication." (expect value v1 on at least replicas)

6) Demonstrate failure + re-replication (explain: controller detects failure and re-replicates)

```bash
docker compose stop worker2
# watch controller logs for re-replication activity
docker compose logs -f controller
```

Say: "I stopped worker2. The controller detects the missing heartbeat, selects a new replica target and instructs an existing replica to pull missing keys to restore replication factor."  After a short wait, query workers to show replica count restored.

Record a demo (asciinema)
-------------------------
If you prefer to record a demo you can use asciinema to capture a reproducible terminal session.

1) Install asciinema (Linux example):

```bash
# Debian/Ubuntu
sudo apt install asciinema
# or via pip: pip install asciinema
```

2) Record the demo (this captures all commands + output):

```bash
asciinema rec kvstore-demo.cast
# run the demo commands (start compose, put/get, stop worker, show logs)
# press Ctrl-D to finish recording
```

3) Play back the recording locally:

```bash
asciinema play kvstore-demo.cast
```

Notes:
- asciinema recordings are plain files you can attach to a submission or replay locally; they don't include your terminal font or window size, but they capture commands and output reliably.
- If you need a short pre-recorded demo I can prepare a recommended command sequence to run while recording.

