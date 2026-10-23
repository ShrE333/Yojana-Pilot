"""Benchmark candidate models on Scheme Saathi's three LLM jobs. Uses the network / local GPU.

    python scripts/bench_llm.py                          # default candidate list
    python scripts/bench_llm.py local:nemotron-3-nano gemini:gemini-3.5-flash

Jobs: (1) profile extraction from a Hindi sentence, (2) situation analysis (planner),
(3) scheme-rule extraction from a cached official page. Prints latency, output tokens/s and
whether the JSON validated, so roles can be assigned from data instead of guesses.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.fields import profile_json_schema  # noqa: E402
from ingest.extract import EXTRACT_SCHEMA, SYSTEM as EXTRACT_SYSTEM, field_catalogue  # noqa: E402
from llm import LLMError, get_llm  # noqa: E402

DEFAULT = ["openai:openai/gpt-oss-120b", "openai:openai/gpt-oss-20b", "gemini:gemini-3.5-flash",
           "gemini:gemini-3.1-pro-preview", "local:nemotron-3-nano", "local:qwen2.5:7b-instruct"]

PROFILE_Q = "मेरी उम्र 45 है, मैं बीड महाराष्ट्र में विधवा हूँ, दो बेटियाँ हैं, खेत मज़दूरी करती हूँ, SC हूँ, सालाना 60 हज़ार कमाई"
PLAN_SCHEMA = {
    "type": "object",
    "properties": {"needs": {"type": "array", "minItems": 2, "items": {
        "type": "object",
        "properties": {"need": {"type": "string"}, "agencies": {"type": "array", "items": {"type": "string"}},
                       "why": {"type": "string"}},
        "required": ["need", "agencies", "why"]}}},
    "required": ["needs"],
}
PLAN_PROMPT = ("Profile: 45-year-old SC widow in Beed, Maharashtra, landless farm labourer, two daughters "
               "aged 8 and 14, family income Rs 60,000/year. List her distinct needs and which Maharashtra "
               "government agencies (departments, Zilla Parishad, welfare boards, development corporations) "
               "are likely to run lesser-known schemes for each need. Do not decide eligibility.")


def cached_page_text() -> str:
    for p in sorted((ROOT / "cache" / "web").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if "sjsa.maharashtra.gov.in" in d["final_url"] and len(d["text"]) > 1500:
            return d["text"][:8000]
    return ""


def run(llm, messages, system, schema, max_tokens):
    t0 = time.perf_counter()
    try:
        r = llm.complete(messages, system=system, json_schema=schema, max_tokens=max_tokens)
        dt = time.perf_counter() - t0
        return f"ok   {dt:6.1f}s {r.usage.get('output_tokens', 0) / dt:6.1f} tok/s"
    except LLMError as e:
        return f"FAIL {time.perf_counter() - t0:6.1f}s {str(e)[:70]}"


def main() -> int:
    for stream in (sys.stdout,):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    candidates = sys.argv[1:] or DEFAULT
    page = cached_page_text()
    print(f"{'model':40} {'profile':32} {'planner':32} {'extract page':32}")
    for spec in candidates:
        provider, _, model = spec.partition(":")
        try:
            llm = get_llm("planner", provider=provider, model=model)
        except LLMError as e:
            print(f"{spec:40} cannot start: {e}")
            continue
        a = run(llm, [{"role": "user", "content": PROFILE_Q}], "Extract stated facts only; null if not stated.",
                profile_json_schema(brief=True), 1200)
        b = run(llm, [{"role": "user", "content": PLAN_PROMPT}], "You plan research. Output JSON only.",
                PLAN_SCHEMA, 1500)
        c = run(llm, [{"role": "user", "content": f"Page text:\n{page}"}],
                EXTRACT_SYSTEM.format(fields=field_catalogue()), EXTRACT_SCHEMA, 3000) if page else "no cached page"
        print(f"{spec:40} {a:32} {b:32} {c:32}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
