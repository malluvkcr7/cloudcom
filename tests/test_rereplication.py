import os
import subprocess
import time
import requests
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
KV_DIR = os.path.abspath(os.path.join(ROOT))
PYTHON = sys.executable

import os
import subprocess
import time
import requests
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
KV_DIR = os.path.abspath(os.path.join(ROOT))
PYTHON = sys.executable


def start_process(cmd, env=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.Popen(cmd, cwd=KV_DIR, env=e)


def wait_http(url, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def count_key_on_workers(key, ports):
    found = 0
    addrs = []
    for p in ports:
        try:
            r = requests.get(f'http://127.0.0.1:{p}/kv/{key}', timeout=1)
            if r.status_code == 200:
                found += 1
                addrs.append(p)
        except Exception:
            pass
    return found, addrs


def test_rereplication():
    ctrl_port = 8200
    ports = [8201, 8202, 8203, 8204]

    env = os.environ.copy()
    env['PYTHONPATH'] = KV_DIR

    # start controller with 4 workers
    env_ctrl = env.copy()
    env_ctrl['WORKERS'] = (
        f"http://localhost:{ports[0]},http://localhost:{ports[1]},"
        f"http://localhost:{ports[2]},http://localhost:{ports[3]}"
    )
    env_ctrl['REPLICAS'] = '3'
    ctrl_cmd = [PYTHON, '-m', 'uvicorn', 'controller:app', '--host', '127.0.0.1', '--port', str(ctrl_port)]
    ctrl = start_process(ctrl_cmd, env=env_ctrl)

    # start 4 workers
    workers = []
    for i, port in enumerate(ports, start=1):
        env_w = env.copy()
        env_w['CONTROLLER'] = f"http://127.0.0.1:{ctrl_port}"
        env_w['ADDRESS'] = f"http://127.0.0.1:{port}"
        env_w['ID'] = f"w{i}"
        env_w['WRITE_QUORUM'] = '2'
        cmd = [PYTHON, '-m', 'uvicorn', 'worker:app', '--host', '127.0.0.1', '--port', str(port)]
        p = start_process(cmd, env=env_w)
        workers.append(p)

    try:
        assert wait_http(f'http://127.0.0.1:{ctrl_port}/health', timeout=8)
        for port in ports:
            assert wait_http(f'http://127.0.0.1:{port}/health', timeout=8)

        # put a key
        r = requests.put(f'http://127.0.0.1:{ports[0]}/kv/r1', json={'value': 'v1'}, timeout=5)
        assert r.status_code == 200

        # get controller mapping and ensure all mapped replicas store the key
        m = requests.get(f'http://127.0.0.1:{ctrl_port}/map', params={'key': 'r1'}, timeout=3).json()
        replicas = m.get('replicas', [])
        # explicitly replicate key to all mapped replicas to ensure full replication for the test
        for addr in replicas:
            success = False
            for _ in range(5):
                try:
                    requests.post(f"{addr}/replicate/r1", json={'value': 'v1'}, timeout=2)
                except Exception:
                    pass
                # check if stored
                try:
                    rr = requests.get(f"{addr}/kv/r1", timeout=1)
                    if rr.status_code == 200:
                        success = True
                        break
                except Exception:
                    pass
                time.sleep(0.2)
            # best effort; continue

        # verify we have REPLICAS copies (allow retries)
        found = 0
        for _ in range(10):
            found, _ = count_key_on_workers('r1', ports)
            if found >= int(env_ctrl.get('REPLICAS', '3')):
                break
            time.sleep(0.2)
        assert found >= int(env_ctrl.get('REPLICAS', '3')), f"expected at least {env_ctrl.get('REPLICAS')} replicas, found {found}"
        # map replica addresses to our port numbers and pick one to kill (not primary if possible)
        port_map = {f'http://127.0.0.1:{p}': p for p in ports}
        kill_port = None
        for addr in replicas:
            if addr in port_map:
                kill_port = port_map[addr]
                break
        assert kill_port is not None, f"no replica found among workers for mapping: {replicas}"
        # terminate the corresponding process
        kill_idx = ports.index(kill_port)
        workers[kill_idx].terminate()
        workers[kill_idx].wait(timeout=5)
        time.sleep(1)

        # wait for controller to detect and re-replicate (timeout 15s)
        deadline = time.time() + 15
        success = False
        while time.time() < deadline:
            found, addrs = count_key_on_workers('r1', ports)
            if found >= 3:
                success = True
                break
            time.sleep(0.5)

        assert success, f"re-replication failed, replicas={found}, addrs={addrs}"

    finally:
        for p in workers:
            try:
                p.kill()
            except Exception:
                pass
        try:
            ctrl.kill()
        except Exception:
            pass
