"""A booking API on localhost for tests of the HTTP client. Made-up data only."""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

TOKEN = "test-token"
API_KEY = "test-key"


class LocalBookingServer:
    def __init__(self, equipment: dict[str, Any]) -> None:  # Any: mirrors the API's JSON.
        self.equipment = equipment
        self.logins = 0
        self.queries: list[dict[str, list[str]]] = []
        self.url = ""


@contextmanager
def local_booking_server(equipment: dict[str, Any]) -> Iterator[LocalBookingServer]:  # Any: mirrors the API's JSON.
    state = LocalBookingServer(equipment)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # Any: matches the base class.
            pass

        def _reply(self, status: int, body: Any) -> None:  # Any: JSON body.
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/login" and body == {"apiKey": API_KEY}:
                state.logins += 1
                self._reply(200, {"accessToken": TOKEN})
            else:
                self._reply(401, {"error": "Unauthorized"})

        def do_GET(self) -> None:
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self._reply(401, {"error": "Unauthorized"})
                return
            url = urlsplit(self.path)
            state.queries.append(parse_qs(url.query))
            item = state.equipment.get(url.path.removeprefix("/api/equipment/"))
            if item is None:
                self._reply(404, {"error": "Not Found"})
            else:
                self._reply(200, item)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
