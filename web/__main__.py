"""Run the web app: python -m web [--port 8000] [--no-browser]"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import threading
import webbrowser

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m web")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if not 1 <= args.port <= 65535:
        ap.error("--port must be between 1 and 65535")
    url = f"http://127.0.0.1:{args.port}"
    # Reserve the port before opening a browser. Otherwise a failed second
    # launch can open the UI of an older process that already owns the port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", args.port))
        except OSError as exc:
            ap.exit(1, f"Cannot start Yojana-Pilot on {url}: {exc}\n"
                    f"Inspect the existing listener with: netstat -ano -p tcp | findstr :{args.port}\n"
                    "Stop only the confirmed Yojana-Pilot server, or choose --port 8001.\n")

        from .app import app

        class WebsiteServer(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                if self.started and not args.no_browser:
                    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

        print(f"Yojana-Pilot | PID {os.getpid()} | {Path(__file__).resolve().parent / 'app.py'}", flush=True)
        print(f"Public site: {url}/ | Dashboard: {url}/app | Ctrl+C to stop", flush=True)
        server = WebsiteServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info"))
        server.run(sockets=[listener])


if __name__ == "__main__":
    main()
