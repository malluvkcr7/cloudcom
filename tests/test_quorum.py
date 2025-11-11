import os
import subprocess
import time
import requests
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
KV_DIR = os.path.abspath(os.path.join(ROOT))
PYTHON = sys.executable

def start_process(cmd, env=None):
    # ensure child inherits PATH and VENV
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


def test_quorum_write():
    # ports
    ctrl_port = 8100
    w1_port = 8101
    w2_port = 8102
    w3_port = 8103

    env = os.environ.copy()
    env['PYTHONPATH'] = KV_DIR

    # start controller with worker list pointing to the three workers
    env_ctrl = env.copy()
    env_ctrl['WORKERS'] = f"http://localhost:{w1_port},http://localhost:{w2_port},http://localhost:{w3_port}"
    env_ctrl['REPLICAS'] = '3'
    ctrl_cmd = [PYTHON, '-m', 'uvicorn', 'controller:app', '--host', '127.0.0.1', '--port', str(ctrl_port)]
    ctrl = start_process(ctrl_cmd, env=env_ctrl)

    # start three workers
    workers = []
    for i, port in enumerate([w1_port, w2_port, w3_port], start=1):
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
        for port in [w1_port, w2_port, w3_port]:
            assert wait_http(f'http://127.0.0.1:{port}/health', timeout=8)

        # 1) put key while all workers are up -> should succeed
        r = requests.put(f'http://127.0.0.1:{w1_port}/kv/q1', json={'value':'v1'}, timeout=5)
        assert r.status_code == 200
        assert r.json().get('acks', 0) >= 2

        # 2) stop worker2 and put another key -> should still succeed (w1 + w3)
        workers[1].terminate()
        workers[1].wait(timeout=5)
        time.sleep(1)
        r2 = requests.put(f'http://127.0.0.1:{w1_port}/kv/q2', json={'value':'v2'}, timeout=5)
        assert r2.status_code == 200
        assert r2.json().get('acks', 0) >= 2

        # 3) stop worker3 too, leaving only w1 -> new put should fail due to insufficient acks
        workers[2].terminate()
        workers[2].wait(timeout=5)
        time.sleep(1)
        r3 = requests.put(f'http://127.0.0.1:{w1_port}/kv/q3', json={'value':'v3'}, timeout=5)
        assert r3.status_code == 503

    finally:
        # cleanup
        for p in workers:
            try:
                p.kill()
            except Exception:
                pass
        try:
            ctrl.kill()
        except Exception:
            pass