"""Try other providers when one cannot answer, telling apart two very different failures.

    <ROLE>_FALLBACKS=gemini:gemini-3.5-flash,openai:openai/gpt-oss-120b     (chain, in order)
    LLM_FALLBACKS=...                                                       (any role)
    LLM_FALLBACK_PROVIDER / LLM_FALLBACK_MODEL                              (single, older form)

- **Busy** (503 overloaded, timeout, connection dropped): the provider is fine, it is just loaded
  right now. Wait a moment and ask it again; only move on after a few tries. No long cooldown,
  because during a long batch the provider usually recovers within seconds.
- **Capped** (429 rate limit, quota spent, request too large): asking again will not help, so move
  to the next provider and leave this one alone for COOLDOWN_SECONDS.

Anything else (bad JSON after the retry, refusals, a wrong key) is raised, never hidden.
"""
from __future__ import annotations

import sys
import time

from .base import LLMClient, LLMError, LLMResponse

COOLDOWN_SECONDS = 600
BUSY_TRIES = 3
BUSY_BACKOFF = (2, 6, 15)          # seconds to wait before asking the same provider again

_BUSY = ("503", "overloaded", "unavailable", "timed out", "timeout", "connection error",
         "connecterror", "connection refused", "temporarily")
_CAPPED = ("429", "ratelimit", "rate limit", "rate_limit", "quota", "resource_exhausted",
           "413", "too large", "insufficient_quota")


def is_busy_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(m in msg for m in _BUSY) and not any(m in msg for m in _CAPPED)


def is_capacity_error(e: Exception) -> bool:
    """Either kind of "cannot answer now": used to decide whether to fall back at all."""
    msg = str(e).lower()
    return any(m in msg for m in _CAPPED + _BUSY)


class FallbackClient:
    def __init__(self, primary: LLMClient, *others: LLMClient, notify=lambda m: print(m, file=sys.stderr),
                 sleep=time.sleep):
        self.clients = [primary, *others]
        self.role = getattr(primary, "role", "default")
        self.notify = notify
        self.sleep = sleep
        self._skip_until = [0.0] * len(self.clients)

    @property
    def primary(self) -> LLMClient:
        return self.clients[0]

    @property
    def secondary(self) -> LLMClient:
        return self.clients[1]

    def _order(self) -> list[int]:
        now = time.monotonic()
        ready = [i for i in range(len(self.clients)) if self._skip_until[i] <= now]
        return ready or [len(self.clients) - 1]           # all capped: try the last one anyway

    @property
    def active(self) -> LLMClient:
        return self.clients[self._order()[0]]

    @property
    def name(self) -> str:
        return self.active.name

    @property
    def model(self) -> str:
        return getattr(self.active, "model", self.active.name)

    def _ask(self, client: LLMClient, messages: list[dict], kwargs: dict) -> LLMResponse:
        """One provider, retried while it is merely busy."""
        for attempt in range(BUSY_TRIES):
            try:
                return client.complete(messages, **kwargs)
            except LLMError as e:
                if not is_busy_error(e) or attempt == BUSY_TRIES - 1:
                    raise
                wait = BUSY_BACKOFF[min(attempt, len(BUSY_BACKOFF) - 1)]
                self.notify(f"({client.name} is busy; trying again in {wait}s)")
                self.sleep(wait)
        raise LLMError("unreachable")

    def complete(self, messages: list[dict], **kwargs) -> LLMResponse:
        order = self._order()
        last_error: LLMError | None = None
        for pos, i in enumerate(order):
            client = self.clients[i]
            try:
                return self._ask(client, messages, kwargs)
            except LLMError as e:
                if not is_capacity_error(e):
                    raise
                last_error = e
                # a busy provider is worth asking again soon; a capped one is not
                self._skip_until[i] = time.monotonic() + (30 if is_busy_error(e) else COOLDOWN_SECONDS)
                if pos + 1 < len(order):
                    self.notify(f"({client.name} unavailable; using {self.clients[order[pos + 1]].name})")
        raise last_error
