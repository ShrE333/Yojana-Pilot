"""The LLM interface every module uses, plus the shared JSON-validation and logging logic.

Providers subclass ProviderBase and implement only _call(). Everything else
(JSON parsing, schema validation, one retry with the error, call logging)
lives here so it behaves the same for every vendor.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import jsonschema

from .log import log_call


class LLMError(RuntimeError):
    """Raised when a provider fails or returns output that is still invalid after the retry."""


@dataclass
class LLMResponse:
    text: str
    data: dict | None          # parsed JSON when json_schema was given (validated)
    model: str
    usage: dict = field(default_factory=dict)


@runtime_checkable
class LLMClient(Protocol):
    name: str                  # "anthropic:claude-sonnet-5-5"

    def complete(self, messages: list[dict], *, system: str | None = None,
                 json_schema: dict | None = None, temperature: float = 0.0,
                 max_tokens: int = 2000) -> LLMResponse: ...


@dataclass
class RawResult:
    """What a provider's _call() returns."""
    text: str
    model: str
    usage: dict = field(default_factory=dict)   # {"input_tokens": int, "output_tokens": int}


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def parse_json(text: str):
    """Parse model output as JSON, tolerating a ```json fence or text around one object."""
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _json_instruction(schema: dict) -> str:
    return ("Reply with one JSON object only, no prose and no code fence. "
            "It must validate against this JSON schema:\n" + json.dumps(schema, ensure_ascii=False))


class ProviderBase:
    provider: str = ""

    def __init__(self, model: str, role: str = "default"):
        self.model = model
        self.role = role

    @property
    def name(self) -> str:
        return f"{self.provider}:{self.model}"

    def _call(self, messages: list[dict], system: str | None, json_mode: bool,
              temperature: float, max_tokens: int) -> RawResult:
        raise NotImplementedError

    def complete(self, messages: list[dict], *, system: str | None = None,
                 json_schema: dict | None = None, temperature: float = 0.0,
                 max_tokens: int = 2000) -> LLMResponse:
        if json_schema is not None:
            system = f"{system}\n\n{_json_instruction(json_schema)}" if system else _json_instruction(json_schema)
        msgs = list(messages)
        usage = {"input_tokens": 0, "output_tokens": 0}
        t0 = time.perf_counter()
        error = None
        attempts = 0
        ok = False
        try:
            # one try, plus one retry that shows the model its validation error
            for attempts in (1, 2):
                raw = self._call(msgs, system, json_schema is not None, temperature, max_tokens)
                for k in usage:
                    usage[k] += int(raw.usage.get(k) or 0)
                if json_schema is None:
                    ok = True
                    return LLMResponse(raw.text, None, raw.model, usage)
                try:
                    data = parse_json(raw.text)
                    jsonschema.validate(data, json_schema)
                    ok = True
                    return LLMResponse(raw.text, data, raw.model, usage)
                except (json.JSONDecodeError, jsonschema.ValidationError) as e:
                    error = _short_error(e)
                    msgs = msgs + [
                        {"role": "assistant", "content": raw.text},
                        {"role": "user", "content": f"Your reply was not valid: {error}. "
                                                    "Reply again with the corrected JSON object only."},
                    ]
            raise LLMError(f"{self.name}: invalid JSON after retry: {error}")
        except LLMError as e:
            error = str(e)
            raise
        except Exception as e:  # vendor SDK errors: wrap so callers handle one type
            error = f"{type(e).__name__}: {e}"
            raise LLMError(f"{self.name}: {error}") from e
        finally:
            # message text is never logged, so no PII reaches the log
            log_call(provider=self.provider, model=self.model, role=self.role, attempts=attempts,
                     latency_ms=round((time.perf_counter() - t0) * 1000), usage=usage,
                     ok=ok, error=None if ok and attempts == 1 else error)


def _short_error(e: Exception) -> str:
    if isinstance(e, jsonschema.ValidationError):
        path = "/".join(str(p) for p in e.absolute_path) or "(root)"
        return f"schema error at {path}: {e.message}"
    return f"not JSON: {e}"
