import threading
import webbrowser
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any


class HTTPRequestHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        # Only shut down after the actual log file is fetched, not on favicon/other requests
        if self.path.endswith(".log"):
            # Defer shutdown so the response finishes first
            threading.Timer(2.0, self.server.shutdown).start()  # type: ignore[attr-defined]
        return super().do_GET()

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        return super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


def open_visualizer(output_file: Path, visualizer_url: str | None = None) -> None:
    if visualizer_url is None:
        # Use local visualizer (forked from jmerle's P3 visualizer with Frankfurt Hedgehogs features)
        # Start with: cd visualiser && pnpm dev
        # Falls back to jmerle's hosted P3 visualizer if local is not running
        visualizer_url = "http://localhost:5173/imc-prosperity-3-visualizer/"

    http_handler = partial(HTTPRequestHandler, directory=str(output_file.parent))
    http_server = HTTPServer(("localhost", 0), http_handler)

    webbrowser.open(
        f"{visualizer_url}?open=http://localhost:{http_server.server_port}/{output_file.name}"
    )

    # serve_forever blocks until http_server.shutdown() is called from the request handler
    # (after the .log file is fetched). Safety timeout kills it after 2 minutes if nothing happens.
    shutdown_timer = threading.Timer(120.0, http_server.shutdown)
    shutdown_timer.daemon = True
    shutdown_timer.start()

    try:
        http_server.serve_forever()
    finally:
        shutdown_timer.cancel()
        http_server.server_close()
