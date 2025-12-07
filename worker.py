from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import threading
import time
import requests
from typing import Dict, List
import random

app = FastAPI(title="kv-worker")
STORE: Dict[str, str] = {}

CONTROLLER = os.environ.get("CONTROLLER", "http://localhost:8000")
ADDRESS = os.environ.get("ADDRESS", "http://localhost:8001")
ID = os.environ.get("ID", "w0")
WRITE_QUORUM = int(os.environ.get("WRITE_QUORUM", "2"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "2"))
# data directory for persistence; if not provided use a per-worker folder
DATA_DIR = os.environ.get("DATA_DIR")
if not DATA_DIR:
    # default to a local folder that includes the worker ID so local tests
    # and multiple workers don't clobber each other
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
    # Coordinator behavior: store locally, then synchronously replicate to peers until
    # we have WRITE_QUORUM acknowledgements (including local store).
    STORE[key] = kv.value
    # persist locally
    _persist_key(key, kv.value)
    ack_count = 1  # self
    attempted = set()
    max_controller_retries = 5
    controller_retries = 0
    backoff = 0.3

    # Keep querying controller for an updated replica map and try newly
    # reported replicas until we reach WRITE_QUORUM or exhaust retries.
    while ack_count < WRITE_QUORUM and controller_retries < max_controller_retries:
        try:
            r = requests.get(f"{CONTROLLER}/map", params={"key": key}, timeout=REQUEST_TIMEOUT)
            data = r.json()
            replicas = data.get("replicas", [])
        except Exception:
            # controller unreachable — wait and retry a couple times
            controller_retries += 1
            time.sleep(backoff)
            continue

        # build list of candidate addresses excluding self and already attempted
        candidates = []
        seen = set()
        for addr in replicas:
            a = addr.rstrip("/")
            if a == ADDRESS.rstrip("/"):
                continue
            if a in seen:
                continue
            seen.add(a)
            if a in attempted:
                continue
            candidates.append(a)

        if not candidates:
            # no new candidates; bump retry counter and wait briefly
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
                resp = requests.post(f"{addr}/replicate/{key}", json={"value": kv.value}, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    ack_count += 1
                    any_success = True
            except Exception:
                # unreachable — try next candidate
                pass

        if not any_success:
            controller_retries += 1
            time.sleep(backoff)

    if ack_count >= WRITE_QUORUM:
        return {"result": "ok", "acks": ack_count}
    else:
        # not enough replicas acknowledged
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
                STORE[k] = r.json().get("value")
        except Exception:
            pass
    return {"result": "pulled", "count": len(req.keys)}

@app.get("/kv/{key}")
def get_key(key: str):
    if key in STORE:
        return {"value": STORE[key]}
    raise HTTPException(status_code=404, detail="not found")

@app.get("/health")
def health():
    return {"status": "worker up", "address": ADDRESS, "stored_keys": len(STORE)}
