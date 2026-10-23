"""Turn one fetched official page into a scheme dict in the curated YAML schema.

The LLM (role extractor) reads the page and proposes rules. Code then decides what
survives (CLAUDE.md section 6):
- a rule must use a field from fields.yaml with a value of the right type
- a rule's `source_quote` must really appear in the page text, else it is dropped
- a dropped rule makes the scheme "check manually", never looser without a warning
- criteria with no matching field go to `unmapped_criteria` (also quote-checked)
- every result is origin: discovered, verified: false
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rapidfuzz import fuzz

from core.fields import load_fields
from core.loader import VALID_OPS, SchemeError, _validate, _validate_rule
from llm import LLMClient, LLMError

from .fetch import Page, host_of

MAX_PAGE_CHARS = 15000
DROPPED_NOTE = ("Some conditions on the official page could not be read automatically; "
                "check the full eligibility on the source page.")

RULE = {
    "type": "object",
    "properties": {
        "field": {"type": "string"},
        "op": {"enum": sorted(VALID_OPS)},
        "value": {},
        "reason": {"type": "string"},
        "source_quote": {"type": "string"},
    },
    "required": ["field", "op", "value", "source_quote"],
}
QUOTED_TEXT = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "source_quote": {"type": "string"}},
    "required": ["text", "source_quote"],
}
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_scheme_page": {"type": "boolean"},
        "name": {"type": ["string", "null"]},
        "name_hi": {"type": ["string", "null"]},
        "level": {"enum": ["central", "state", None]},
        "is_district_specific": {"type": ["boolean", "null"]},
        "state": {"type": ["string", "null"]},
        "ministry": {"type": ["string", "null"]},
        "benefit_summary": {"type": ["string", "null"]},
        "benefit_annual_inr": {"type": ["number", "null"]},
        "value_score": {"type": ["integer", "null"], "minimum": 1, "maximum": 5},
        "effort": {"type": ["integer", "null"], "minimum": 1, "maximum": 5},
        "all_rules": {"type": "array", "items": RULE},
        "any_rules": {"type": "array", "items": RULE},
        "unmapped_criteria": {"type": "array", "items": QUOTED_TEXT},
        "documents": {"type": "array", "items": {"type": "string"}},
        "how_to_apply": {"type": ["string", "null"]},
        "apply_url": {"type": ["string", "null"]},
        "mentioned_schemes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["is_scheme_page"],
}

SYSTEM = """You read one Indian government web page and extract ONE welfare scheme's eligibility rules as data.

Rules you must follow:
- Use ONLY the profile fields listed below. Never invent a field name.
- Each rule: {{field, op, value, reason, source_quote}}. op is one of eq ne gt gte lt lte in not_in.
  value must match the field type (true/false for bool, a number for int/number, one of the listed
  values for enum; a list of them for in/not_in). Money in rupees per year for annual_family_income_inr.
- source_quote: copy the exact sentence or phrase from the page that states the rule. Copy, do not paraphrase.
- all_rules: conditions that must all hold. any_rules: alternatives where one is enough (else []).
- Age conditions are ALWAYS age rules, never unmapped: "senior citizen" = age gte 60, "18 to 40 years" =
  age gte 18 and age lte 40. Income limits per month become annual_family_income_inr (x12).
- A condition that fits no field goes to unmapped_criteria as {{text, source_quote}}.
- If the page is not about one specific scheme (a list, a news item, a login page), set
  is_scheme_page false and put the scheme names it mentions in mentioned_schemes.
- level: "state" if only residents of one state can apply (give state in plain words), else "central".
- is_district_specific: true ONLY if the scheme is run by one district (Zilla Parishad / district
  administration) and people of other districts cannot apply. A state or central scheme that merely
  appears on a district website is false. If unsure, false.
- benefit_annual_inr: yearly cash value in rupees if stated, else null. value_score 1-5 (how much it helps
  a poor family), effort 1-5 (how hard to apply). documents: names of documents required.
- Never decide whether any person is eligible. Only extract what the page says.

Profile fields:
{fields}"""


@dataclass
class ExtractResult:
    scheme: dict | None
    mentioned_schemes: list[str] = field(default_factory=list)
    kept_rules: int = 0
    dropped: list[str] = field(default_factory=list)      # human-readable reasons
    error: str | None = None


def field_catalogue() -> str:
    lines = []
    for name, spec in load_fields().items():
        if spec["group"] == "dependency":
            continue
        kind = spec["type"] + (f": {', '.join(spec['values'])}" if spec.get("values") else "")
        lines.append(f"- {name} ({kind}): {spec['description']}")
    return "\n".join(lines)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def quote_found(quote: str, norm_text: str) -> bool:
    q = _norm(quote).strip(" .\"'“”")
    if len(q) < 10:
        return False
    if q in norm_text:
        return True
    return fuzz.partial_ratio(q, norm_text) >= 92     # tolerate punctuation / spacing changes


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:60].strip("_")


def normalise_state(raw: str | None) -> str | None:
    if not raw:
        return None
    s = re.sub(r"[^a-z]+", "_", raw.lower().replace("&", "and")).strip("_")
    aliases = {"nct_of_delhi": "delhi", "orissa": "odisha", "j_and_k": "jammu_and_kashmir",
               "pondicherry": "puducherry", "uttaranchal": "uttarakhand"}
    s = aliases.get(s, s)
    return s if s in load_fields()["state"]["values"] else None


def _default_near_miss(rule: dict) -> None:
    """Deterministic tolerance so "almost eligible" works for scraped rules too."""
    if rule["op"] not in ("gt", "gte", "lt", "lte") or isinstance(rule["value"], bool):
        return
    if rule["field"] == "age":
        rule["near_miss"] = 2
    elif rule["field"] == "annual_family_income_inr" and rule["op"] in ("lt", "lte"):
        rule["near_miss"] = round(rule["value"] * 0.1)


_AGE_RANGE = re.compile(r"(\d{2})\s*(?:-|to|and|–|से)\s*(\d{2,3})\s*(?:years|yrs|वर्ष|साल)", re.I)
_AGE_MIN = re.compile(r"(\d{2})\s*(?:years|yrs|वर्ष|साल)?\s*(?:of age\s*)?(?:or|and|&)\s*(?:above|more|older|over)"
                      r"|(?:above|over|minimum age of|at least)\s*(\d{2})\s*(?:years|yrs)", re.I)
_AGE_MAX = re.compile(r"(?:below|under|less than|up to|upto|not more than|maximum age of)\s*(\d{2,3})\s*(?:years|yrs)", re.I)
_SENIOR = re.compile(r"senior citizen|वरिष्ठ नागरिक|ज्येष्ठ नागरिक|old age", re.I)
# the profile describes the applicant; an age limit about someone else must not become an age rule
_OTHER_PERSON = re.compile(r"\b(girl|girls|daughter|son|child|children|boy|boys|student|students|"
                           r"ward|infant|baby|minor)\b|मुलगी|मुलगा|बालक|बालिका|बेटी|बेटा|बच्च|विद्यार्थी", re.I)


def promote_unmapped(scheme: dict) -> int:
    """Turn clear age wording left in unmapped_criteria into age rules (deterministic, no LLM).
    Only when the scheme has no age rule yet. Returns the number of rules added."""
    rules = scheme["eligibility"].setdefault("all", [])
    if any(r["field"] == "age" for r in rules + list(scheme["eligibility"].get("any") or [])):
        return 0
    added, keep = [], []
    for text in scheme.get("unmapped_criteria") or []:
        new = []
        if _OTHER_PERSON.search(text):
            pass
        elif m := _AGE_RANGE.search(text):
            lo, hi = int(m.group(1)), int(m.group(2))
            if 0 < lo < hi <= 120:
                new = [("gte", lo), ("lte", hi)]
        elif m := _AGE_MIN.search(text):
            new = [("gte", int(m.group(1) or m.group(2)))]
        elif m := _AGE_MAX.search(text):
            new = [("lte", int(m.group(1)))]
        elif _SENIOR.search(text):
            new = [("gte", 60)]
        if new and not added:
            for op, value in new:
                added.append({"field": "age", "op": op, "value": value, "reason": text, "near_miss": 2,
                              "source_quote": text, "promoted_from_unmapped": True})
        else:
            keep.append(text)
    if added:
        rules[:0] = added
        scheme["unmapped_criteria"] = keep
    return len(added)


def _clean_rules(raw_rules: list[dict], norm_text: str, fields: dict, res: ExtractResult,
                 unmapped: list[str]) -> tuple[list[dict], bool]:
    """Return (kept rules, whether any rule was dropped)."""
    kept, dropped_any = [], False
    for r in raw_rules or []:
        quote = r.get("source_quote", "")
        if not quote_found(quote, norm_text):
            res.dropped.append(f"quote not on page: {r.get('field')} ({quote[:60]!r})")
            dropped_any = True
            continue
        rule = {"field": r["field"], "op": r["op"], "value": r["value"],
                "reason": r.get("reason") or quote[:200], "source_quote": quote}
        if isinstance(rule["value"], float) and rule["value"].is_integer():
            rule["value"] = int(rule["value"])
        try:
            _validate_rule(rule, fields, "extract")
        except SchemeError as e:
            # the page says it, but no field fits: keep it as text for a human
            res.dropped.append(f"unmappable: {e}")
            unmapped.append(_norm(rule["reason"]) or quote)
            dropped_any = True
            continue
        _default_near_miss(rule)
        kept.append(rule)
    return kept, dropped_any


def build_scheme(data: dict, page: Page, model: str) -> ExtractResult:
    res = ExtractResult(scheme=None, mentioned_schemes=list(data.get("mentioned_schemes") or []))
    if not data.get("is_scheme_page") or not (data.get("name") or "").strip():
        res.error = "not a single-scheme page"
        return res
    fields = load_fields()
    norm_text = _norm(page.text)
    unmapped: list[str] = []
    for u in data.get("unmapped_criteria") or []:
        if quote_found(u.get("source_quote", ""), norm_text):
            unmapped.append(u["text"].strip())
        else:
            res.dropped.append(f"unmapped criterion quote not on page: {u.get('text', '')[:60]!r}")

    all_rules, dropped_all = _clean_rules(data.get("all_rules"), norm_text, fields, res, unmapped)
    any_rules, dropped_any = _clean_rules(data.get("any_rules"), norm_text, fields, res, unmapped)
    if dropped_any:
        # removing an alternative would make the scheme stricter than the page: keep none of them
        any_rules = []
    if dropped_all or dropped_any:
        unmapped.append(DROPPED_NOTE)

    name = data["name"].strip()
    state = normalise_state(data.get("state")) if data.get("level") == "state" else None
    if data.get("level") == "state" and state is None and data.get("state"):
        unmapped.append(f"Only for residents of {data['state']}")
    district_specific = bool(data.get("is_district_specific"))
    sid = slugify(name) or "scheme_" + page.content_hash[:8]
    if state:
        sid = f"{sid}__{state}"
    now = datetime.now(timezone.utc)
    benefit = data.get("benefit_annual_inr")
    scheme = {
        "id": sid,
        "name": name,
        **({"name_hi": data["name_hi"]} if data.get("name_hi") else {}),
        "origin": "discovered",
        "verified": False,
        "state": state,
        "district": None,                 # set from the source map only for district-run schemes
        "district_specific": district_specific,
        "ministry": data.get("ministry"),
        "source_url": page.final_url,
        "source_site": host_of(page.final_url),
        "last_verified": page.fetched_at[:10],
        "fetched_at": page.fetched_at,
        "extracted_at": now.isoformat(timespec="seconds"),
        "extractor_model": model,
        "content_hash": page.content_hash,
        "benefit_summary": data.get("benefit_summary") or "",
        "benefit_annual_inr": int(benefit) if isinstance(benefit, (int, float)) and benefit > 0 else 0,
        "rank": {"value_score": data.get("value_score") or 3, "effort": data.get("effort") or 3},
        "eligibility": {"all": all_rules, **({"any": any_rules} if any_rules else {})},
        "unmapped_criteria": list(dict.fromkeys(u for u in unmapped if u)),
        "documents": [{"name": d, "issued_by": "", "key": slugify(d) or f"doc{i}"}
                      for i, d in enumerate(data.get("documents") or []) if str(d).strip()],
        "how_to_apply": data.get("how_to_apply") or "",
        "apply_url": data.get("apply_url") or "",
        "dependencies": [],
        "records_to_match": [],
        "actions": {},
        "status_map": {},
    }
    promote_unmapped(scheme)
    try:
        _validate(scheme, Path(f"{sid}.yaml"), fields)
    except SchemeError as e:
        res.error = f"invalid scheme: {e}"
        return res
    res.scheme = scheme
    res.kept_rules = len(all_rules) + len(any_rules)
    return res


def extract_scheme(page: Page, llm: LLMClient) -> ExtractResult:
    text = page.text[:MAX_PAGE_CHARS]
    try:
        resp = _ask_extractor(llm, page, text)
    except LLMError as e:
        if "413" not in str(e) and "too large" not in str(e).lower():
            raise
        text = page.text[:MAX_PAGE_CHARS // 3]          # free tiers cap request size: retry shorter
        resp = _ask_extractor(llm, page, text)
    # quotes are checked against the same text the model saw
    page_seen = Page(**{**page.__dict__, "text": text})
    return build_scheme(resp.data, page_seen, getattr(llm, "model", llm.name))


def _ask_extractor(llm: LLMClient, page: Page, text: str):
    prompt = f"Page URL: {page.final_url}\nPage title: {page.title}\n\nPage text:\n{text}"
    return llm.complete([{"role": "user", "content": prompt}], system=SYSTEM.format(fields=field_catalogue()),
                        json_schema=EXTRACT_SCHEMA, max_tokens=3000)
