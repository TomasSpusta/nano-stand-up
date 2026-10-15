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
    def __init__(
        self,
        instruments: dict[str, Any],  # Any: mirrors the API's JSON.
        login_answer: dict[str, Any],  # Any: mirrors the API's JSON.
        token_uses: int | None,
    ) -> None:
        self.instruments = instruments
        self.login_answer = login_answer
        # How many requests each token is good for, or None for no limit.
        self.token_uses = token_uses
        # Each token handed out and the requests it has left.
        self.tokens_left: dict[str, int] = {}
        self.logins = 0
        # Logins tried, with the right API key or not.
        self.login_attempts = 0
        self.queries: list[dict[str, list[str]]] = []
        self.url = ""


@contextmanager
def local_booking_server(
    instruments: dict[str, Any],  # Any: mirrors the API's JSON.
    login_answer: dict[str, Any] | None = None,  # Any: mirrors the API's JSON.
    token_uses: int | None = None,
) -> Iterator[LocalBookingServer]:
    """`login_answer` replaces the normal login answer, which holds the access token.

    With `token_uses`, each login hands out a new token that expires after that many requests.
    """
    state = LocalBookingServer(
        instruments, {"accessToken": TOKEN} if login_answer is None else login_answer, token_uses
    )

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
            state.login_attempts += 1
            if self.path == "/api/login" and body == {"apiKey": API_KEY}:
                state.logins += 1
                if state.token_uses is None:
                    self._reply(200, state.login_answer)
                else:
                    token = f"{TOKEN}-{state.logins}"
                    state.tokens_left[token] = state.token_uses
                    self._reply(200, {"accessToken": token})
            else:
                self._reply(401, {"error": "Unauthorized"})

        def _authorized(self) -> bool:
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
            if state.token_uses is None:
                return token == TOKEN
            if state.tokens_left.get(token, 0) <= 0:
                return False
            state.tokens_left[token] -= 1
            return True

        def do_GET(self) -> None:
            if not self._authorized():
                self._reply(401, {"error": "Unauthorized"})
                return
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            state.queries.append(query)
            if url.path == "/api/equipment":
                # The whole list on page 1, without filters. Enough for tests of the wiring.
                self._reply(200, list(state.instruments.values()) if query.get("page") == ["1"] else [])
                return
            item = state.instruments.get(url.path.removeprefix("/api/equipment/"))
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
