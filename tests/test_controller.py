from fastapi.testclient import TestClient
import pytest

from controller import app as controller_app

client = TestClient(controller_app)

def test_map_returns_replicas():
    resp = client.get("/map", params={"key": "foo"})
    assert resp.status_code == 200
    j = resp.json()
    assert "primary" in j and "replicas" in j
    assert len(j["replicas"]) >= 1

