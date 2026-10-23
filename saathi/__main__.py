"""Interactive Scheme Saathi in the terminal.

    python -m saathi                         # chat; searches official websites
    python -m saathi "मेरी उम्र 62 है ..."     # start with a first message
    python -m saathi --no-web                # only schemes already saved (no search quota used)
    python -m saathi --debug                 # also print the profile after each turn
"""
from __future__ import annotations

import argparse
import sys

from llm import LLMError

from .chat import Saathi
from .understand import facts_line

QUIT = {"quit", "exit", "bye", "q", "बंद", "बाहेर"}


def main() -> int:
    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")     # Devanagari on Windows terminals
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m saathi")
    ap.add_argument("message", nargs="*", help="optional first message")
    ap.add_argument("--no-web", action="store_true", help="do not search the web")
    ap.add_argument("--max-searches", type=int, default=6)
    ap.add_argument("--max-pages", type=int, default=10)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    bot = Saathi(web=not args.no_web, max_searches=args.max_searches, max_pages=args.max_pages)
    print("\nSaathi> " + bot.greeting() + "\n")
    first = " ".join(args.message).strip()
    while True:
        if first:
            text, first = first, ""
            print(f"You> {text}")
        else:
            try:
                text = input("You> ").replace("﻿", "").strip()   # PowerShell pipes may add a BOM
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
        if not text:
            continue
        if text.lower() in QUIT:
            return 0
        try:
            reply = bot.handle(text)
        except LLMError as e:
            reply = f"(Sorry, the language model failed: {e}. Please try again.)"
        print("\nSaathi> " + reply + "\n")
        if args.debug:
            print(f"   [stage={bot.stage} lang={bot.lang} profile={facts_line(bot.profile)}]\n")


if __name__ == "__main__":
    sys.exit(main())
