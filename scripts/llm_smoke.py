"""Live check that each configured provider answers, in text and in JSON mode. Uses the network.

    python scripts/llm_smoke.py                      # every role as configured in .env
    python scripts/llm_smoke.py --provider gemini    # force one provider for all roles
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import LLMError, get_llm  # noqa: E402
from llm.config import ROLES  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {"age": {"type": "integer"}, "owns_cultivable_land": {"type": "boolean"}},
    "required": ["age", "owns_cultivable_land"],
}
QUERY = "मेरी उम्र 62 है और मेरे पास 2 एकड़ ज़मीन है।"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider")
    ap.add_argument("--model")
    args = ap.parse_args()
    failed = 0
    for role in ROLES:
        try:
            llm = get_llm(role, provider=args.provider, model=args.model)
            text = llm.complete([{"role": "user", "content": "Reply with the single word: namaste"}],
                                max_tokens=20).text.strip()
            data = llm.complete([{"role": "user", "content": QUERY}],
                                system="Extract the person's age and whether they own farm land.",
                                json_schema=SCHEMA, max_tokens=200).data
            print(f"OK   {role:9} {llm.name:40} text={text!r} json={data}")
        except LLMError as e:
            failed += 1
            print(f"FAIL {role:9} {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
