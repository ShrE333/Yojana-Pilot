"""Deterministic provider for tests and offline runs. Never touches the network.

Two ways to script it:
- responses={"key": reply}: the first key found in the system prompt or any message wins
- queue=[reply, reply, ...]: replies returned in order (checked before responses)
A reply may be a string or a dict (sent as JSON). Every call is recorded in .calls.
With LLM_PROVIDER=mock, MOCK_LLM_FILE can point to a YAML file of {key: reply}.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from ..base import LLMError, ProviderBase, RawResult


class MockProvider(ProviderBase):
    provider = "mock"

    def __init__(self, model: str = "mock", role: str = "default",
                 responses: dict | None = None, queue: list | None = None):
        super().__init__(model, role)
        if responses is None and os.getenv("MOCK_LLM_FILE"):
            responses = yaml.safe_load(Path(os.environ["MOCK_LLM_FILE"]).read_text(encoding="utf-8"))
        self.responses = dict(responses or {})
        self.queue = list(queue or [])
        self.calls: list[dict] = []

    def _call(self, messages, system, json_mode, temperature, max_tokens) -> RawResult:
        self.calls.append({"messages": list(messages), "system": system, "json_mode": json_mode})
        haystack = "\n".join([system or ""] + [str(m["content"]) for m in messages])
        if self.queue:
            reply = self.queue.pop(0)
        else:
            reply = next((v for k, v in self.responses.items() if k in haystack), None)
            if reply is None:
                raise LLMError("mock: no canned response matches this prompt")
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return RawResult(text=text, model=self.model,
                         usage={"input_tokens": len(haystack) // 4, "output_tokens": len(text) // 4})
