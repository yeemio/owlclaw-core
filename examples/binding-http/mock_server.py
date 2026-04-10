"""Simple mock HTTP server for declarative binding examples."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ORDERS: dict[str, dict[str, object]] = {
    "A1001": {"order_id": "A1001", "status": "created", "amount": 199.0},
    "A1002": {"order_id": "A1002", "status": "paid", "amount": 88.5},
}


class _Handler(BaseHTTPRequestHandler):
    server_version = "OwlClawMock/1.0"

    def _send_json(self, payload: dict[str, object], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/orders/"):
            order_id = path.split("/", 2)[-1]
            order = ORDERS.get(order_id)
            if order is None:
                self._send_json({"status": "not_found", "order_id": order_id}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({"status": "success", "data": order}, status=HTTPStatus.OK)
            return
        self._send_json({"status": "not_found", "path": path}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/orders":
            self._send_json({"status": "not_found", "path": path}, status=HTTPStatus.NOT_FOUND)
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json({"status": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
            return
        order_id = str(payload.get("order_id", "")).strip()
        if not order_id:
            self._send_json({"status": "bad_request", "message": "order_id is required"}, status=HTTPStatus.BAD_REQUEST)
            return
        ORDERS[order_id] = {
            "order_id": order_id,
            "status": "created",
            "amount": float(payload.get("amount", 0.0)),
        }
        self._send_json({"status": "created", "data": ORDERS[order_id]}, status=HTTPStatus.CREATED)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        # Keep output concise for example runs.
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock HTTP server for binding examples.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8008)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"mock server listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
