from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import threading
import time
import requests
from typing import Dict, List
import random

app = FastAPI(title="kv-worker")

# Add CORS middleware to allow requests from the browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)
STORE: Dict[str, str] = {}

CONTROLLER = os.environ.get("CONTROLLER", "http://localhost:8000")
ADDRESS = os.environ.get("ADDRESS", "http://localhost:8001")
ID = os.environ.get("ID", "w0")
WRITE_QUORUM = int(os.environ.get("WRITE_QUORUM", "2"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "2"))
# data directory for persistence; defaults to /app/data for container volumes
# for local testing, defaults to data_{ID} in current directory
DATA_DIR = os.environ.get("DATA_DIR")
if not DATA_DIR:
    # Check if we're in a container (if /app exists)
    if os.path.isdir("/app"):
        DATA_DIR = "/app/data"
    else:
        # Local testing: use per-worker folder to avoid conflicts
        DATA_DIR = os.path.abspath(os.path.join(os.getcwd(), f"data_{ID}"))

import json
import urllib.parse


def _safe_filename(key: str) -> str:
    return urllib.parse.quote_plus(key)


def _persist_key(key: str, value: str) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        fn = os.path.join(DATA_DIR, _safe_filename(key))
        with open(fn, "w", encoding="utf-8") as f:
            json.dump({"value": value}, f)
    except Exception:
        # best-effort persistence
        pass


def _load_persisted():
    try:
        if not os.path.isdir(DATA_DIR):
            return
        for name in os.listdir(DATA_DIR):
            path = os.path.join(DATA_DIR, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    j = json.load(f)
                    # decode key name
                    key = urllib.parse.unquote_plus(name)
                    STORE[key] = j.get("value")
            except Exception:
                pass
    except Exception:
        pass

class KV(BaseModel):
    value: str

class ReplicateReq(BaseModel):
    value: str

@app.on_event("startup")
def startup():
    # start heartbeat thread
    # load persisted keys if any
    _load_persisted()
    def hb_loop():
        while True:
            try:
                requests.post(f"{CONTROLLER}/heartbeat", json={"id": ID, "address": ADDRESS}, timeout=2)
            except Exception:
                pass
            time.sleep(2)
    t = threading.Thread(target=hb_loop, daemon=True)
    t.start()

@app.put("/kv/{key}")
def put_key(key: str, kv: KV):
    """
    Correct implementation of a write coordinator:
    - Coordinator stores locally ONLY if it is part of the replica set.
    - Performs synchronous replication to reach WRITE_QUORUM.
    - After quorum, asynchronously replicates to remaining replicas.
    """

    # Query controller to get the replica set
    try:
        r = requests.get(
            f"{CONTROLLER}/map",
            params={"key": key},
            timeout=REQUEST_TIMEOUT
        )
        data = r.json()
        replicas = data.get("replicas", [])
    except Exception:
        raise HTTPException(status_code=503, detail="controller unavailable")

    # Normalize addresses
    norm_self = ADDRESS.rstrip("/")
    norm_replicas = [a.rstrip("/") for a in replicas]

    # Determine whether THIS worker is part of the replica set
    is_replica = norm_self in norm_replicas

    # ACK count (local write counts only if node is a replica)
    ack_count = 0
    attempted = set()

    # If this coordinator is a replica, store locally
    if is_replica:
        STORE[key] = kv.value
        _persist_key(key, kv.value)
        ack_count = 1
        attempted.add(norm_self)

    # ---- QUORUM REPLICATION LOOP ----
    controller_retries = 0
    backoff = 0.3
    max_retries = 5

    while ack_count < WRITE_QUORUM and controller_retries < max_retries:

        # Try all replicas not yet attempted
        candidates = [
            addr for addr in norm_replicas
            if addr not in attempted and addr != norm_self
        ]

        if not candidates:
            # No new replicas left to try
            controller_retries += 1
            time.sleep(backoff)
            continue

        random.shuffle(candidates)
        any_success = False

        for addr in candidates:
            if ack_count >= WRITE_QUORUM:
                break

            attempted.add(addr)

            try:
                resp = requests.post(
                    f"{addr}/replicate/{key}",
                    json={"value": kv.value},
                    timeout=REQUEST_TIMEOUT
                )
                if resp.status_code == 200:
                    ack_count += 1
                    any_success = True
            except Exception:
                # Replica unreachable — try next
                pass

        if not any_success:
            controller_retries += 1
            time.sleep(backoff)

    # ---- QUORUM SUCCESS ----
    if ack_count >= WRITE_QUORUM:
        # Background replication to remaining replicas
        def background_replicate():
            for addr in norm_replicas:
                if addr not in attempted:
                    try:
                        requests.post(
                            f"{addr}/replicate/{key}",
                            json={"value": kv.value},
                            timeout=REQUEST_TIMEOUT
                        )
                    except Exception:
                        pass

        threading.Thread(target=background_replicate, daemon=True).start()

        return {"result": "ok", "acks": ack_count}

    # ---- QUORUM FAILURE ----
    raise HTTPException(status_code=503, detail=f"write failed; acks={ack_count}")

@app.post("/replicate/{key}")
def replicate(key: str, req: ReplicateReq):
    STORE[key] = req.value
    # persist replicated value
    _persist_key(key, req.value)
    return {"result": "replicated"}


@app.get("/keys")
def list_keys():
    return {"keys": list(STORE.keys())}


class PullReq(BaseModel):
    source: str
    keys: List[str]


@app.post("/pull")
def pull_from(req: PullReq):
    # Pull keys from source worker and store locally
    for k in req.keys:
        try:
            r = requests.get(f"{req.source}/kv/{k}", timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                value = r.json().get("value")
                STORE[k] = value
                # persist the pulled key
                _persist_key(k, value)
        except Exception:
            pass
    return {"result": "pulled", "count": len(req.keys)}
@app.get("/pull")
def pull():
    return {"result": "pulled", "count": len(STORE)}
@app.delete("/delete/{key}")
def delete_key(key: str):
    if key in STORE:
        del STORE[key]
        # remove persisted key
        try:
            os.remove(os.path.join(DATA_DIR, _safe_filename(key)))
        except Exception:
            pass
    return {"result": "deleted"}
@app.get("/kv/{key}")
def get_key(key: str):
    if key in STORE:
        return {"value": STORE[key]}
    raise HTTPException(status_code=404, detail="not found")

@app.get("/health")
def health():
    return {"status": "worker up", "address": ADDRESS, "stored_keys": len(STORE)}
