from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List
import hashlib
import os
import time
import threading
import requests
import sys
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="kv-controller")

# Add CORS middleware to allow requests from the browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

# Worker registry: id -> {address, last_seen}
workers: Dict[str, Dict] = {}
# Default worker list (comma-separated addresses) for quick start
DEFAULT_WORKERS = os.environ.get("WORKERS", "http://localhost:8001,http://localhost:8002,http://localhost:8003,http://localhost:8004").split(",")
REPLICAS = int(os.environ.get("REPLICAS", "3"))
HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "6"))
CHECK_INTERVAL = float(os.environ.get("CHECK_INTERVAL", "2"))

# track workers considered down to avoid repeated re-replication
down_workers = set()

class Heartbeat(BaseModel):
    id: str
    address: str

@app.on_event("startup")
def startup():
    # Do not pre-populate the worker registry here. Workers will register
    # themselves by POSTing /heartbeat to the controller. We keep
    # DEFAULT_WORKERS available as a fallback when get_mapping is called
    # in test or demo scenarios where pre-seeding is desired.
    return


def primary_index_for_key(key: str, n: int) -> int:
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    return h % n

@app.post("/heartbeat")
def heartbeat(hb: Heartbeat):
    workers[hb.id] = {"address": hb.address, "last_seen": time.time()}
    return {"status": "ok"}

@app.get("/workers")
def list_workers():
    return {wid: {"address": v["address"]} for wid, v in workers.items()}

@app.get("/map")
def get_mapping(key: str):
    # ensure we have a registry (helps in test environment where startup event may
    # not have populated `workers` yet)
    if not workers:
        for i, addr in enumerate(DEFAULT_WORKERS):
            wid = f"w{i}"
            workers[wid] = {"address": addr, "last_seen": time.time()}
    live = [workers[w]["address"] for w in sorted(workers.keys())]
    n = len(live)
    if n == 0:
        raise HTTPException(status_code=503, detail="no available workers")
    primary = primary_index_for_key(key, n)
    indices = [(primary + i) % n for i in range(REPLICAS)]
    replicas = [live[i] for i in indices]
    return {"primary": replicas[0], "replicas": replicas}

@app.get("/health")
def health():
    return {"status": "controller up", "workers_count": len(workers)}


def re_replicate(failed_id: str, snapshot: Dict = None):
    """
    Re-replication that does not rely on a single source node.
    - Queries all live workers (based on snapshot) to build the union of keys.
    - For each key that used to include the failed worker in its replica set,
      chooses a source among live workers that already have the key and
      a target among live workers that don't, then instructs the target to pull.
    """
    logger.info(f"[RE-REPLICATE] Starting re-replication for failed worker: {failed_id}")

    # Use provided snapshot (taken before worker was removed), or take a snapshot now.
    if snapshot is None:
        snapshot = {k: v.copy() for k, v in workers.items()}

    pre_workers = sorted(snapshot.keys())
    try:
        idx_failed = pre_workers.index(failed_id)
    except ValueError:
        logger.info(f"[RE-REPLICATE] Failed worker {failed_id} not in pre_workers list")
        return

    n = len(pre_workers)
    failed_addr = snapshot[failed_id]["address"]

    # current live addresses (exclude failed) as seen in the snapshot
    live = [snapshot[w]["address"] for w in pre_workers if w != failed_id]
    if not live:
        logger.info("[RE-REPLICATE] No live workers to re-replicate to")
        return

    # --- STEP 1: collect union of keys by querying all live workers ---
    all_keys = set()
    worker_keys_map: Dict[str, List[str]] = {}  # addr -> keys list

    for addr in live:
        try:
            r = requests.get(f"{addr}/keys", timeout=3)
            if r.status_code == 200:
                keys = r.json().get("keys", [])
                worker_keys_map[addr] = keys
                all_keys.update(keys)
                logger.info(f"[RE-REPLICATE] {len(keys)} keys from {addr}")
            else:
                logger.info(f"[RE-REPLICATE] /keys returned {r.status_code} from {addr}")
        except Exception as e:
            logger.info(f"[RE-REPLICATE] Failed to query /keys from {addr}: {e}")

    logger.info(f"[RE-REPLICATE] Collected total {len(all_keys)} unique keys from live workers")

    # --- STEP 2: For each key, compute old replica set and handle if failed was a replica ---
    for key in list(all_keys):
        try:
            # compute replicas list as it was before failure (using n = pre-failure cluster size)
            primary = primary_index_for_key(key, n)
            indices = [(primary + i) % n for i in range(REPLICAS)]
            replica_ids = [pre_workers[i] for i in indices]
            replica_addrs = [snapshot[rid]["address"] if rid in snapshot else None for rid in replica_ids]
        except Exception as e:
            logger.info(f"[RE-REPLICATE] Failed to compute old replicas for key '{key}': {e}")
            continue

        # Only care about keys for which the failed worker used to be a replica
        if failed_addr not in replica_addrs:
            continue

        logger.info(f"[RE-REPLICATE] Key '{key}' had failed replica {failed_addr} (old replicas: {replica_addrs})")

        # Determine which live workers already have the key.
        # Prefer workers that reported the key via /keys, but double-check by asking /kv/{key}.
        have = []
        for addr in live:
            # If worker reported the key in /keys earlier, count it first
            reported_has = addr in worker_keys_map and key in worker_keys_map.get(addr, [])
            if reported_has:
                have.append(addr)
                continue

            # otherwise probe with /kv/{key}
            try:
                rr = requests.get(f"{addr}/kv/{key}", timeout=2)
                if rr.status_code == 200:
                    have.append(addr)
            except Exception:
                pass

        # Choose a target: a live worker that does not already have the key
        target = None
        for addr in live:
            if addr not in have:
                target = addr
                break

        # Choose a source: pick one from `have` (prefer random to distribute load)
        src = None
        if have:
            # randomize to avoid hot-source thundering
            import random as _rand
            src = _rand.choice(have)
        else:
            # No live worker seems to have the key (edge case). As fallback,
            # use the first live worker (it will likely fail to GET and nothing happens).
            src = live[0]

        logger.info(f"[RE-REPLICATE] Key '{key}': have={have}, src={src}, target={target}")

        if not target:
            logger.info(f"[RE-REPLICATE] No target found for '{key}' - all live workers already have it")
            continue

        # Instruct target to pull the key from src
        try:
            logger.info(f"[RE-REPLICATE] Instructing {target} to pull '{key}' from {src}")
            resp = requests.post(f"{target}/pull", json={"source": src, "keys": [key]}, timeout=8)
            if resp.status_code == 200:
                logger.info(f"[RE-REPLICATE] Successfully re-replicated '{key}' to {target}")
            else:
                logger.info(f"[RE-REPLICATE] Pull returned {resp.status_code} when re-replicating '{key}' to {target}")
        except Exception as e:
            logger.info(f"[RE-REPLICATE] Failed to re-replicate '{key}' to {target}: {e}")

    logger.info(f"[RE-REPLICATE] Re-replication pass complete for failed worker: {failed_id}")



def watcher_loop():
    while True:
        now = time.time()
        for wid, info in list(workers.items()):
            last = info.get("last_seen", 0)
            if now - last > HEARTBEAT_TIMEOUT and wid not in down_workers:
                # detected down
                logger.info(f"[WATCHER] Detected worker {wid} is down (last seen: {last}, now: {now})")
                down_workers.add(wid)
                # Take snapshot of ALL workers BEFORE removing the failed one
                # so re_replicate can compute correct replica sets
                worker_snapshot_for_rereplicate = {k: v.copy() for k, v in workers.items()}
                # attempt re-replication in background with snapshot
                threading.Thread(target=re_replicate, args=(wid, worker_snapshot_for_rereplicate), daemon=True).start()
                # remove worker from registry so mapping reflects live set
                try:
                    del workers[wid]
                except KeyError:
                    pass
        time.sleep(CHECK_INTERVAL)


@app.on_event("startup")
def start_watcher():
    t = threading.Thread(target=watcher_loop, daemon=True)
    t.start()

# Mount static files for the web UI
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
def root():
    """Serve the main UI"""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))
