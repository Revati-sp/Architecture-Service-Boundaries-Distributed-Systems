import pytest

import inventory_service


@pytest.fixture
def client():
    inventory_service.app.config["TESTING"] = True
    return inventory_service.app.test_client()


def test_item_available(client):
    resp = client.get("/inventory/items/item-1/availability?quantity=2")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "item_id": "item-1",
        "requested_quantity": 2,
        "available": True,
        "available_quantity": 10,
    }


def test_item_out_of_stock(client):
    resp = client.get("/inventory/items/item-3/availability?quantity=1")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["available"] is False
    assert body["available_quantity"] == 0


def test_requested_more_than_stock(client):
    resp = client.get("/inventory/items/item-2/availability?quantity=6")
    assert resp.status_code == 200
    assert resp.get_json()["available"] is False


def test_unknown_item_returns_404(client):
    resp = client.get("/inventory/items/item-999/availability?quantity=1")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Item not found"


@pytest.mark.parametrize("query", ["", "?quantity=", "?quantity=0", "?quantity=-3", "?quantity=abc"])
def test_invalid_quantity_returns_400(client, query):
    resp = client.get(f"/inventory/items/item-1/availability{query}")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Invalid request"


def test_availability_check_does_not_change_stock(client):
    before = dict(inventory_service.INVENTORY)
    client.get("/inventory/items/item-1/availability?quantity=3")
    assert inventory_service.INVENTORY == before
