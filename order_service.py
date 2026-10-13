"""Order Service.

Accepts order commands, asks the Inventory Service (over HTTP) whether the
item is available, and stores the orders it creates. Runs as its own process
on port 5002 (override with the ORDER_PORT environment variable).
"""

import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [order-service] %(levelname)s %(message)s",
)
logger = logging.getLogger("order_service")

INVENTORY_SERVICE_URL = os.environ.get("INVENTORY_SERVICE_URL", "http://127.0.0.1:5001")
# (connect timeout, read timeout) in seconds for calls to the Inventory Service.
INVENTORY_TIMEOUT = (2, 3)

# Order data owned by this service. In-memory, so it resets on restart.
ORDERS = {}
ORDERS_LOCK = threading.Lock()

app = Flask(__name__)


class InventoryUnavailable(Exception):
    """The Inventory Service could not be reached or timed out."""


class InventoryBadResponse(Exception):
    """The Inventory Service answered with something we cannot use."""


def error_response(status, error, message):
    return jsonify({"error": error, "message": message}), status


def validate_order(payload):
    """Return an error message for an invalid order payload, or None if valid."""
    if not isinstance(payload, dict):
        return "Request body must be a JSON object"
    item_id = payload.get("item_id")
    if not isinstance(item_id, str) or not item_id.strip():
        return "item_id is required and must be a non-empty string"
    quantity = payload.get("quantity")
    # bool is a subclass of int in Python, so reject it explicitly.
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        return "quantity is required and must be a positive integer"
    return None


def check_inventory(item_id, quantity):
    """Query the Inventory Service. Returns (status_code, json_body)."""
    url = f"{INVENTORY_SERVICE_URL}/inventory/items/{quote(item_id, safe='')}/availability"
    try:
        response = requests.get(url, params={"quantity": quantity}, timeout=INVENTORY_TIMEOUT)
    except requests.exceptions.Timeout as exc:
        raise InventoryUnavailable(f"timed out calling {url}") from exc
    except requests.exceptions.ConnectionError as exc:
        raise InventoryUnavailable(f"connection failed to {url}") from exc

    if response.status_code >= 500:
        raise InventoryBadResponse(f"status {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise InventoryBadResponse("response was not valid JSON") from exc
    return response.status_code, body


def publish_order_created(order):
    """Record (log) an "OrderCreated" event.

    Placeholder for a message broker: a Notification Service could consume this
    event later. For now the event is only logged, so order creation never
    depends on notifications succeeding.
    """
    logger.info(
        "EVENT OrderCreated order_id=%s item_id=%s quantity=%d",
        order["order_id"], order["item_id"], order["quantity"],
    )


@app.get("/health")
def health():
    return jsonify({"service": "order", "status": "ok"})


@app.post("/orders")
def create_order():
    """Command: create an order if the Inventory Service reports stock."""
    payload = request.get_json(silent=True)
    validation_error = validate_order(payload)
    if validation_error:
        logger.warning("Rejected order request: %s", validation_error)
        return error_response(400, "Invalid request", validation_error)

    item_id = payload["item_id"].strip()
    quantity = payload["quantity"]

    try:
        status_code, inventory = check_inventory(item_id, quantity)
    except InventoryUnavailable as exc:
        logger.error("Inventory Service unavailable: %s", exc)
        return error_response(
            503, "Inventory service unavailable", "Could not contact the Inventory Service"
        )
    except InventoryBadResponse as exc:
        logger.error("Bad response from Inventory Service: %s", exc)
        return error_response(
            502, "Bad gateway", "The Inventory Service returned an invalid response"
        )

    if status_code == 404:
        return error_response(404, "Item not found", f"Item '{item_id}' does not exist")
    if status_code != 200 or not isinstance(inventory.get("available"), bool):
        logger.error("Unexpected inventory reply: status=%s body=%s", status_code, inventory)
        return error_response(
            502, "Bad gateway", "The Inventory Service returned an invalid response"
        )

    if not inventory["available"]:
        logger.info("Order rejected: insufficient stock for %s (requested %d)", item_id, quantity)
        return jsonify({
            "error": "Insufficient inventory",
            "message": f"Requested {quantity} of '{item_id}' but only "
                       f"{inventory.get('available_quantity', 0)} available",
            "item_id": item_id,
            "requested_quantity": quantity,
            "available_quantity": inventory.get("available_quantity", 0),
        }), 409

    order = {
        "order_id": str(uuid.uuid4()),
        "status": "created",
        "item_id": item_id,
        "quantity": quantity,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with ORDERS_LOCK:
        ORDERS[order["order_id"]] = order
    logger.info("Order %s created for %d x %s", order["order_id"], quantity, item_id)
    publish_order_created(order)
    return jsonify(order), 201


@app.get("/orders/<order_id>")
def get_order(order_id):
    with ORDERS_LOCK:
        order = ORDERS.get(order_id)
    if order is None:
        return error_response(404, "Order not found", f"Order '{order_id}' does not exist")
    return jsonify(order)


@app.errorhandler(HTTPException)
def handle_http_error(exc):
    return error_response(exc.code, exc.name, exc.description)


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    # Log the full traceback server-side; never send it to the client.
    logger.exception("Unhandled error")
    return error_response(500, "Internal server error", "An unexpected error occurred")


if __name__ == "__main__":
    port = int(os.environ.get("ORDER_PORT", "5002"))
    logger.info("Starting Order Service on port %d (inventory at %s)", port, INVENTORY_SERVICE_URL)
    app.run(host="127.0.0.1", port=port)
