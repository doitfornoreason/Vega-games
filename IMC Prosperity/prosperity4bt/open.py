import webbrowser
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any


class HTTPRequestHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.shutdown_flag = True  # type: ignore[attr-defined]
        return super().do_GET()

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        return super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


class CustomHTTPServer(HTTPServer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.shutdown_flag = False


def open_visualizer(output_file: Path, visualizer_url: str | None = None) -> None:
    if visualizer_url is None:
        # Use local visualizer (forked from jmerle's P3 visualizer with Frankfurt Hedgehogs features)
        # Start with: cd visualiser && pnpm dev
        # Falls back to jmerle's hosted P3 visualizer if local is not running
        visualizer_url = "http://localhost:5173/imc-prosperity-3-visualizer/"

    http_handler = partial(HTTPRequestHandler, directory=str(output_file.parent))
    http_server = CustomHTTPServer(("localhost", 0), http_handler)

    webbrowser.open(
        f"{visualizer_url}?open=http://localhost:{http_server.server_port}/{output_file.name}"
    )

    while not http_server.shutdown_flag:
        http_server.handle_request()
