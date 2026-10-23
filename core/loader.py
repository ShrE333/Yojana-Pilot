"""Load scheme YAML files and the question bank."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from .fields import NUMERIC, FieldError, check_value, load_fields

SCHEMES_DIR = Path(__file__).resolve().parent.parent / "schemes"
REQUIRED_KEYS = {"id", "name", "source_url", "last_verified", "eligibility", "rank", "actions", "status_map"}
VALID_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in"}
ORDER_OPS = {"gt", "gte", "lt", "lte"}
NOT_SCHEMES = {"questions.yaml", "fields.yaml", "flagship.yaml"}


class SchemeError(ValueError):
    """Raised when a scheme file is malformed."""


def _validate_rule(rule: dict, fields: dict, where: str) -> None:
    """The field must be in fields.yaml and the value must fit its type for the op."""
    name, op = rule["field"], rule["op"]
    spec = fields.get(name)
    if spec is None:
        raise SchemeError(f"{where}: unknown field {name!r}; use a field from fields.yaml "
                          "or put the criterion in unmapped_criteria")
    value = rule["value"]
    try:
        if op in ("in", "not_in"):
            if not isinstance(value, list) or not value:
                raise SchemeError(f"{where}: op {op} on {name!r} needs a non-empty list")
            for v in value:
                check_value(name, v, fields)
        else:
            if op in ORDER_OPS and spec["type"] not in NUMERIC:
                raise SchemeError(f"{where}: op {op} needs a numeric field, {name!r} is {spec['type']}")
            check_value(name, value, fields)
    except FieldError as e:
        raise SchemeError(f"{where}: {e}") from e
    if "near_miss" in rule and (op not in ORDER_OPS or isinstance(rule["near_miss"], bool)
                                or not isinstance(rule["near_miss"], (int, float))):
        raise SchemeError(f"{where}: near_miss needs a number and a gt/gte/lt/lte rule on {name!r}")


def _validate(scheme: dict, path: Path, fields: dict | None = None) -> None:
    fields = fields or load_fields()
    missing = REQUIRED_KEYS - scheme.keys()
    if missing:
        raise SchemeError(f"{path.name}: missing keys {sorted(missing)}")
    elig = scheme["eligibility"]
    # a web-discovered scheme may have no mappable rule; it is shown as "check manually"
    if not (elig.get("all") or elig.get("any")) and scheme.get("origin") != "discovered":
        raise SchemeError(f"{path.name}: eligibility needs 'all' or 'any'")
    if scheme.get("state") is not None:
        try:
            check_value("state", scheme["state"], fields)
        except FieldError as e:
            raise SchemeError(f"{path.name}: {e}") from e
    for rule in (elig.get("all") or []) + (elig.get("any") or []):
        if rule.get("op") not in VALID_OPS:
            raise SchemeError(f"{path.name}: bad op {rule.get('op')!r} on field {rule.get('field')!r}")
        if "field" not in rule or "value" not in rule:
            raise SchemeError(f"{path.name}: rule needs 'field' and 'value': {rule}")
        _validate_rule(rule, fields, path.name)
    # criteria with no matching field: kept as text, checked by a human, never auto-passed
    unmapped = scheme.get("unmapped_criteria", [])
    if not isinstance(unmapped, list) or not all(isinstance(c, str) and c.strip() for c in unmapped):
        raise SchemeError(f"{path.name}: unmapped_criteria must be a list of non-empty strings")
    # every action referenced in status_map / dependencies must exist
    actions = scheme["actions"]
    for status, causes in scheme["status_map"].items():
        for c in causes:
            if c["action"] not in actions:
                raise SchemeError(f"{path.name}: status {status!r} uses unknown action {c['action']!r}")
    for dep in scheme.get("dependencies", []):
        if dep["id"] not in fields:
            raise SchemeError(f"{path.name}: dependency {dep['id']!r} is not a field in fields.yaml")
        if dep["fix"] not in actions:
            raise SchemeError(f"{path.name}: dependency {dep['id']!r} uses unknown action {dep['fix']!r}")


def load_scheme_dir(folder: Path, skip_invalid: bool = False) -> dict[str, dict]:
    """Uncached load of every scheme YAML in a folder. skip_invalid drops bad files (web data)."""
    fields = load_fields()
    schemes: dict[str, dict] = {}
    for path in sorted(Path(folder).glob("*.yaml")):
        if path.name in NOT_SCHEMES:
            continue
        try:
            scheme = yaml.safe_load(path.read_text(encoding="utf-8"))
            _validate(scheme, path, fields)
        except (SchemeError, yaml.YAMLError, KeyError, TypeError, AttributeError):
            if skip_invalid:
                continue
            raise
        if scheme["id"] in schemes:
            raise SchemeError(f"duplicate scheme id {scheme['id']!r}")
        schemes[scheme["id"]] = scheme
    return schemes


def load_schemes(directory: str | None = None) -> dict[str, dict]:
    """Every scheme in the database. Callers that know the person's state should use
    discovery.catalog.catalog_for(profile) instead, which reads far fewer rows."""
    if directory is not None:
        return load_scheme_dir(Path(directory))
    from store import db
    return db.load_catalog()


@lru_cache(maxsize=None)
def load_questions(directory: str | None = None) -> tuple[dict, ...]:
    folder = Path(directory) if directory else SCHEMES_DIR
    questions = tuple(yaml.safe_load((folder / "questions.yaml").read_text(encoding="utf-8")))
    fields = load_fields()
    for q in questions:
        unknown = [f for f in q["fields"] if f not in fields]
        if unknown:
            raise SchemeError(f"questions.yaml: question {q['id']!r} sets unknown fields {unknown}")
        for f, v in (q.get("on_no") or {}).items():
            try:
                check_value(f, v, fields)
            except FieldError as e:
                raise SchemeError(f"questions.yaml: question {q['id']!r} on_no: {e}") from e
    return questions
