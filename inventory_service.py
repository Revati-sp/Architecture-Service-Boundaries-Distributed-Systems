"""Inventory Service.

Owns item stock levels and answers availability queries. Runs as its own
process on port 5001 (override with the INVENTORY_PORT environment variable).
"""

import logging
import os

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [inventory-service] %(levelname)s %(message)s",
)
logger = logging.getLogger("inventory_service")

# Sample in-memory data. Only this service reads or writes it.
INVENTORY = {
    "item-1": 10,
    "item-2": 5,
    "item-3": 0,
}

app = Flask(__name__)


def error_response(status, error, message):
    return jsonify({"error": error, "message": message}), status


def parse_positive_int(raw):
    """Return raw as a positive int, or None if it is not one."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


@app.get("/health")
def health():
    return jsonify({"service": "inventory", "status": "ok"})


@app.get("/inventory/items/<item_id>/availability")
def check_availability(item_id):
    """Query: is `quantity` of `item_id` in stock? Never changes state."""
    raw_quantity = request.args.get("quantity")
    quantity = parse_positive_int(raw_quantity)
    if quantity is None:
        logger.warning("Rejected availability check for %s: quantity=%r", item_id, raw_quantity)
        return error_response(400, "Invalid request", "quantity must be a positive integer")

    if item_id not in INVENTORY:
        logger.info("Availability check for unknown item %s", item_id)
        return error_response(404, "Item not found", f"Item '{item_id}' does not exist")

    available_quantity = INVENTORY[item_id]
    available = available_quantity >= quantity
    logger.info(
        "Availability check item=%s requested=%d in_stock=%d available=%s",
        item_id, quantity, available_quantity, available,
    )
    return jsonify({
        "item_id": item_id,
        "requested_quantity": quantity,
        "available": available,
        "available_quantity": available_quantity,
    })


@app.errorhandler(HTTPException)
def handle_http_error(exc):
    return error_response(exc.code, exc.name, exc.description)


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    # Log the full traceback server-side; never send it to the client.
    logger.exception("Unhandled error")
    return error_response(500, "Internal server error", "An unexpected error occurred")


if __name__ == "__main__":
    port = int(os.environ.get("INVENTORY_PORT", "5001"))
    logger.info("Starting Inventory Service on port %d", port)
    app.run(host="127.0.0.1", port=port)
