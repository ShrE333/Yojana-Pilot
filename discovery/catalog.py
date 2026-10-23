"""The catalog the engine decides on. Backed by the database (store/db.py), not by files.

    load_catalog(state="maharashtra", district="beed")  -> {id: scheme} the engine should consider
    save_discovered(scheme)                             -> (id, "new" | "updated" | ...)

Kept as its own module so callers do not need to know where schemes are stored.
"""
from __future__ import annotations

from core.awareness import norm_name
from store import db

__all__ = ["load_catalog", "save_discovered", "find_duplicate", "norm_name"]


def load_catalog(state: str | None = None, district: str | None = None,
                 include_discovered: bool = True, path=None) -> dict[str, dict]:
    return db.load_catalog(state, district, include_discovered, path)


def catalog_for(profile: dict, path=None) -> dict[str, dict]:
    """Only the schemes that can apply to this person: central + their state + their district."""
    return db.load_catalog(profile.get("state"), profile.get("district"), path=path)


def save_discovered(scheme: dict, path=None) -> tuple[str, str]:
    return db.save_scheme(scheme, path)


def find_duplicate(scheme: dict, path=None) -> str | None:
    return db.find_duplicate(scheme, path)
