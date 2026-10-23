"""Run the web app: python -m web [--port 8000] [--no-browser]"""
from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m web")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    url = f"http://localhost:{args.port}"
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Scheme Saathi web: {url}  (Ctrl+C to stop)")
    uvicorn.run("web.app:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
