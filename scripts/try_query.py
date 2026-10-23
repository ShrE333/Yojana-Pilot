"""Try the pipeline on one free-text query (uses the network for the LLM step).

    python scripts/try_query.py "मेरी उम्र 62 है, 2 एकड़ ज़मीन है, BPL कार्ड है"

Step 1: LLM (role extractor) turns the sentence into profile fields.
Step 2: code checks those fields against schemes/fields.yaml.
Step 3: the deterministic engine decides eligibility, ranks, and picks the next question.
A temporary script until the real conversation loop (python -m saathi) is built in Phase E.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import assist  # noqa: E402
from core.fields import profile_json_schema, validate_profile  # noqa: E402
from llm import LLMError, get_llm  # noqa: E402

SYSTEM = ("Extract only facts the person states about themself or their family. "
          "Use null for anything not stated. Never guess.")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    query = " ".join(sys.argv[1:])
    llm = get_llm("extractor")
    print(f"[1] LLM {llm.name} extracting fields ...")
    try:
        r = llm.complete([{"role": "user", "content": query}], system=SYSTEM,
                         json_schema=profile_json_schema(), max_tokens=1500)
    except LLMError as e:
        print("LLM failed:", e)
        return 1
    extracted = {k: v for k, v in r.data.items() if v is not None}

    profile, errors = validate_profile(extracted)
    print(f"[2] Profile after validation ({len(profile)} fields):")
    print("    " + json.dumps(profile, ensure_ascii=False))
    for e in errors:
        print("    dropped:", e)

    out = assist(profile)
    print("[3] Engine decision (no LLM involved):")
    for res in out["results"]:
        print(f"    - {res['why']}")
    print(f"    Start with: {out['start_with']}   Queue: {out['queue']}")
    if out["next_question"]:
        print(f"    Next question to ask: {out['next_question']['en']}")
    print(f"\n(tokens: {r.usage})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
