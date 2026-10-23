"""Documents needed for a scheme, marking which ones the user already has."""
from __future__ import annotations

from .loader import load_schemes


def build_checklist(scheme_id: str, have: set[str] | None = None, schemes: dict | None = None) -> list[dict]:
    schemes = schemes or load_schemes()
    have = have or set()
    return [{"document": d["name"], "key": d["key"], "where_to_get": d["issued_by"],
             "status": "have" if d["key"] in have else "missing"}
            for d in schemes[scheme_id].get("documents", [])]
