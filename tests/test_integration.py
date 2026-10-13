"""End-to-end test with both services running as real, separate processes.

Uses ports 5101/5102 so it does not clash with services you may have running
on the default ports 5001/5002.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
INVENTORY_PORT = 5101
ORDER_PORT = 5102
ORDER_URL = f"http://127.0.0.1:{ORDER_PORT}"


def start(script, env_overrides):
    env = {**os.environ, **env_overrides}
    return subprocess.Popen(
        [sys.executable, str(ROOT / script)], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def wait_until_healthy(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"http://127.0.0.1:{port}/health", timeout=0.5).status_code == 200:
                return
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"service on port {port} did not start")


def stop(proc):
    proc.terminate()
    proc.wait(timeout=5)


def start_inventory():
    proc = start("inventory_service.py", {"INVENTORY_PORT": str(INVENTORY_PORT)})
    wait_until_healthy(INVENTORY_PORT)
    return proc


@pytest.fixture
def services():
    inventory = start_inventory()
    order = start("order_service.py", {
        "ORDER_PORT": str(ORDER_PORT),
        "INVENTORY_SERVICE_URL": f"http://127.0.0.1:{INVENTORY_PORT}",
    })
    wait_until_healthy(ORDER_PORT)
    procs = {"inventory": inventory, "order": order}
    yield procs
    for proc in procs.values():
        if proc.poll() is None:
            stop(proc)


def place_order(item_id, quantity):
    return requests.post(f"{ORDER_URL}/orders", json={"item_id": item_id, "quantity": quantity}, timeout=10)


def test_order_flow_across_processes(services):
    assert place_order("item-1", 2).status_code == 201
    assert place_order("item-3", 1).status_code == 409
    assert place_order("item-1", 0).status_code == 400


def test_inventory_outage_and_recovery(services):
    assert place_order("item-1", 2).status_code == 201

    stop(services["inventory"])
    resp = place_order("item-1", 2)
    assert resp.status_code == 503
    assert resp.json()["error"] == "Inventory service unavailable"

    # The Order Service process is still alive and healthy.
    assert services["order"].poll() is None
    assert requests.get(f"{ORDER_URL}/health", timeout=2).status_code == 200

    services["inventory"] = start_inventory()
    assert place_order("item-1", 2).status_code == 201
