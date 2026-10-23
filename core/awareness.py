"""How widely known a scheme is: "high" | "medium" | "low". Deterministic, no LLM.

high   = on schemes/flagship.yaml (PM-KISAN, PMJAY, Ujjwala, ...)
low    = run by a district (Zilla Parishad / collector), a welfare board or a development corporation
medium = everything else (state department and central department schemes)
A scheme may set `awareness:` itself (e.g. after a human review); that wins.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml
from rapidfuzz import fuzz

FLAGSHIP_FILE = Path(__file__).resolve().parent.parent / "schemes" / "flagship.yaml"
LOW_AGENCIES = {"district", "welfare_board", "corporation"}
OBSCURITY = {"high": 0, "medium": 1, "low": 2}

_FILLER = r"\b(scheme|schemes|yojana|yojna|abhiyan|the|of|for|and|programme|program|mission|ki|ka|ke)\b"


def norm_name(name: str) -> str:
    s = name.lower().replace("pradhan mantri", "pm").replace("pradhanmantri", "pm")
    s = re.sub(r"[^a-z0-9ऀ-ॿ ]+", " ", s)
    s = re.sub(_FILLER, " ", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=None)
def flagship_names() -> tuple[str, ...]:
    return tuple(norm_name(n) for n in yaml.safe_load(FLAGSHIP_FILE.read_text(encoding="utf-8")))


def is_flagship(name: str) -> bool:
    key = norm_name(name)
    return any(fuzz.token_set_ratio(key, f) >= 90 for f in flagship_names())


def awareness(scheme: dict) -> str:
    if scheme.get("awareness") in OBSCURITY:
        return scheme["awareness"]
    if is_flagship(scheme["name"]):
        return "high"
    if scheme.get("agency_type") in LOW_AGENCIES or scheme.get("district"):
        return "low"
    return "medium"
