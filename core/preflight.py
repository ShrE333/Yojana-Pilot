"""Pre-flight check: catch problems BEFORE the person applies.

preflight_check(scheme_id, records, profile) where
    records = {"aadhaar": {"name": "Sunil Patil", "dob": "1975"},
               "land_record": {"name": "Suneel Patil"}, ...}
    profile has dependency flags like ekyc_done=True/False (None = unknown)

Returns an ordered list of fixes plus every comparison it made.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .loader import load_schemes
from .name_match import compare_dob, compare_names

# order fixes so the cheapest/earliest blocker comes first
FIX_ORDER = ["fix_name_mismatch", "reactivate_account", "npci_seeding", "complete_ekyc",
             "land_seeding", "make_card"]


@dataclass
class RecordCheck:
    record_a: str
    record_b: str
    field: str                  # "name" or "dob"
    verdict: str
    notes: list[str] = field(default_factory=list)
    differing_tokens: list = field(default_factory=list)


@dataclass
class Fix:
    action: str
    text: str
    where: str
    because: str


@dataclass
class PreflightResult:
    scheme_id: str
    ready: bool
    fixes: list[Fix]
    checks: list[RecordCheck]
    unknown_dependencies: list[str]
    flags: set[str]             # e.g. {"name_mismatch", "npci_not_seeded"} for the status decoder


DEP_FLAG = {"bank_aadhaar_npci_seeded": "npci_not_seeded",
            "bank_account_active": "account_inactive",
            "ekyc_done": "ekyc_not_done",
            "land_record_seeded": "land_not_seeded"}


def preflight_check(scheme_id: str, records: dict | None, profile: dict | None = None,
                    schemes: dict | None = None) -> PreflightResult:
    schemes = schemes or load_schemes()
    scheme = schemes[scheme_id]
    records = records or {}
    profile = profile or {}
    actions = scheme["actions"]
    checks: list[RecordCheck] = []
    fixes: dict[str, Fix] = {}
    flags: set[str] = set()

    # 1. names and DOBs across documents
    for a, b in scheme.get("records_to_match", []):
        ra, rb = records.get(a), records.get(b)
        if not ra or not rb:
            continue
        if ra.get("name") and rb.get("name"):
            nc = compare_names(ra["name"], rb["name"])
            checks.append(RecordCheck(a, b, "name", nc.verdict, nc.notes, nc.differing_tokens))
            if nc.verdict != "match":
                flags.add("name_mismatch")
                why = f"name on {a} ('{ra['name']}') and {b} ('{rb['name']}') differ: {'; '.join(nc.notes)}"
                act = actions.get("fix_name_mismatch")
                if act:
                    if "fix_name_mismatch" in fixes:
                        fixes["fix_name_mismatch"].because += f" | {why}"
                    else:
                        fixes["fix_name_mismatch"] = Fix("fix_name_mismatch", act["text"], act["where"], why)
        if ra.get("dob") and rb.get("dob"):
            verdict, note = compare_dob(ra["dob"], rb["dob"])
            checks.append(RecordCheck(a, b, "dob", verdict, [note] if note else []))
            if verdict in ("mismatch", "probable_mismatch"):
                flags.add("dob_mismatch")
                why = f"date of birth on {a} and {b}: {note}"
                key = "fix_dob_mismatch"
                fixes[key] = Fix(key, "Make the date of birth the same on all documents",
                                 "The office that issued the document with the wrong date "
                                 "(Aadhaar Seva Kendra for Aadhaar, bank branch for the passbook)", why)

    # 2. dependency chain
    unknown = []
    for dep in scheme.get("dependencies", []):
        val = profile.get(dep["id"])
        if val is None:
            unknown.append(dep["id"])
        elif val is False:
            flags.add(DEP_FLAG.get(dep["id"], f"{dep['id']}_missing"))
            act = actions[dep["fix"]]
            fixes.setdefault(dep["fix"], Fix(dep["fix"], act["text"], act["where"], f"{dep['label']}: not done"))

    order = {k: i for i, k in enumerate(FIX_ORDER)}
    ordered = sorted(fixes.values(), key=lambda f: order.get(f.action, 0 if f.action == "fix_dob_mismatch" else 99))
    return PreflightResult(scheme_id, ready=not ordered and not unknown, fixes=ordered,
                           checks=checks, unknown_dependencies=unknown, flags=flags)
