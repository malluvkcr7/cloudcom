from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, List
import hashlib
import os
import time
import threading
import requests

app = FastAPI(title="kv-controller")

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


def re_replicate(failed_id: str):
    """Attempt to re-replicate keys for which the failed worker was a replica.
    This is a simple implementation: query one alive worker for keys and for each
    key whose replica set included the failed worker, instruct a target alive
    worker to pull the key from an existing replica.
    """
    # take a snapshot of workers mapping at failure time
    snapshot = {k: v.copy() for k, v in workers.items()}
    pre_workers = sorted(snapshot.keys())
    try:
        idx_failed = pre_workers.index(failed_id)
    except ValueError:
        return
    n = len(pre_workers)
    # current live addresses (exclude failed)
    live = [snapshot[w]["address"] for w in pre_workers if w != failed_id]
    if not live:
        return

    # pick a source worker to enumerate keys
    source_addr = live[0]
    try:
        r = requests.get(f"{source_addr}/keys", timeout=3)
        keys = r.json().get("keys", [])
    except Exception:
        keys = []

    for key in keys:
        # compute replicas list as it was before failure
        primary = primary_index_for_key(key, n)
        indices = [(primary + i) % n for i in range(REPLICAS)]
        replica_ids = [pre_workers[i] for i in indices]
        # map replica ids to addresses using snapshot
        replica_addrs = [snapshot[rid]["address"] if rid in snapshot else None for rid in replica_ids]
        # if failed worker's address was among replicas, we need to replicate elsewhere
        failed_addr = snapshot[failed_id]["address"]
        if failed_addr in replica_addrs:
            # determine which current live workers already have the key
            have = []
            for addr in live:
                try:
                    rr = requests.get(f"{addr}/kv/{key}", timeout=2)
                    if rr.status_code == 200:
                        have.append(addr)
                except Exception:
                    pass
            # choose a target that is live and not in have
            target = None
            for addr in live:
                if addr not in have:
                    target = addr
                    break
            # choose a source replica among have (if any)
            src = have[0] if have else source_addr
            if target:
                try:
                    requests.post(f"{target}/pull", json={"source": src, "keys": [key]}, timeout=5)
                except Exception:
                    pass


def watcher_loop():
    while True:
        now = time.time()
        for wid, info in list(workers.items()):
            last = info.get("last_seen", 0)
            if now - last > HEARTBEAT_TIMEOUT and wid not in down_workers:
                # detected down
                down_workers.add(wid)
                # attempt re-replication in background
                threading.Thread(target=re_replicate, args=(wid,), daemon=True).start()
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
