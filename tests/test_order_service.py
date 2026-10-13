"""Unit tests for the Order Service. The Inventory Service is mocked."""

from unittest import mock

import pytest
import requests

import order_service


@pytest.fixture
def client():
    order_service.app.config["TESTING"] = True
    order_service.ORDERS.clear()
    return order_service.app.test_client()


def inventory_reply(status_code, body):
    """Build a fake requests.Response-like object."""
    reply = mock.Mock()
    reply.status_code = status_code
    reply.json.return_value = body
    return reply


AVAILABLE = inventory_reply(200, {
    "item_id": "item-1", "requested_quantity": 2, "available": True, "available_quantity": 10,
})
UNAVAILABLE = inventory_reply(200, {
    "item_id": "item-3", "requested_quantity": 1, "available": False, "available_quantity": 0,
})


def test_successful_order(client):
    with mock.patch("order_service.requests.get", return_value=AVAILABLE) as fake_get:
        resp = client.post("/orders", json={"item_id": "item-1", "quantity": 2})

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["status"] == "created"
    assert body["item_id"] == "item-1"
    assert body["quantity"] == 2
    assert body["order_id"] in order_service.ORDERS

    # The call to the Inventory Service must use a timeout.
    _, kwargs = fake_get.call_args
    assert kwargs["timeout"] == order_service.INVENTORY_TIMEOUT
    assert kwargs["params"] == {"quantity": 2}


def test_created_order_can_be_fetched(client):
    with mock.patch("order_service.requests.get", return_value=AVAILABLE):
        order_id = client.post("/orders", json={"item_id": "item-1", "quantity": 2}).get_json()["order_id"]
    resp = client.get(f"/orders/{order_id}")
    assert resp.status_code == 200
    assert resp.get_json()["order_id"] == order_id


@pytest.mark.parametrize("payload", [
    {},
    {"quantity": 1},
    {"item_id": "", "quantity": 1},
    {"item_id": 123, "quantity": 1},
    {"item_id": "item-1"},
    {"item_id": "item-1", "quantity": 0},
    {"item_id": "item-1", "quantity": -2},
    {"item_id": "item-1", "quantity": "2"},
    {"item_id": "item-1", "quantity": 1.5},
    {"item_id": "item-1", "quantity": True},
    ["item-1", 2],
])
def test_invalid_input_returns_400(client, payload):
    with mock.patch("order_service.requests.get") as fake_get:
        resp = client.post("/orders", json=payload)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Invalid request"
    fake_get.assert_not_called()  # invalid input never reaches the Inventory Service


def test_malformed_json_returns_400(client):
    resp = client.post("/orders", data="{not json", content_type="application/json")
    assert resp.status_code == 400


def test_unavailable_item_returns_409(client):
    with mock.patch("order_service.requests.get", return_value=UNAVAILABLE):
        resp = client.post("/orders", json={"item_id": "item-3", "quantity": 1})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["error"] == "Insufficient inventory"
    assert body["available_quantity"] == 0
    assert order_service.ORDERS == {}


def test_unknown_item_returns_404(client):
    reply = inventory_reply(404, {"error": "Item not found", "message": "..."})
    with mock.patch("order_service.requests.get", return_value=reply):
        resp = client.post("/orders", json={"item_id": "item-999", "quantity": 1})
    assert resp.status_code == 404


@pytest.mark.parametrize("failure", [
    requests.exceptions.ConnectionError("connection refused"),
    requests.exceptions.ConnectTimeout("connect timed out"),
    requests.exceptions.ReadTimeout("read timed out"),
])
def test_inventory_unreachable_returns_503(client, failure):
    with mock.patch("order_service.requests.get", side_effect=failure):
        resp = client.post("/orders", json={"item_id": "item-1", "quantity": 2})
    assert resp.status_code == 503
    assert resp.get_json() == {
        "error": "Inventory service unavailable",
        "message": "Could not contact the Inventory Service",
    }
    assert order_service.ORDERS == {}


def test_inventory_server_error_returns_502(client):
    with mock.patch("order_service.requests.get", return_value=inventory_reply(500, {})):
        resp = client.post("/orders", json={"item_id": "item-1", "quantity": 2})
    assert resp.status_code == 502


def test_order_service_keeps_working_after_inventory_failure(client):
    """After a 503 the same app keeps serving and recovers once inventory is back."""
    with mock.patch("order_service.requests.get",
                    side_effect=requests.exceptions.ConnectionError("down")):
        assert client.post("/orders", json={"item_id": "item-1", "quantity": 2}).status_code == 503

    assert client.get("/health").status_code == 200

    with mock.patch("order_service.requests.get", return_value=AVAILABLE):
        resp = client.post("/orders", json={"item_id": "item-1", "quantity": 2})
    assert resp.status_code == 201


def test_unexpected_error_hides_stack_trace(client):
    with mock.patch("order_service.requests.get", side_effect=RuntimeError("secret internals")):
        resp = client.post("/orders", json={"item_id": "item-1", "quantity": 2})
    assert resp.status_code == 500
    assert "secret internals" not in resp.get_data(as_text=True)
    assert "Traceback" not in resp.get_data(as_text=True)
