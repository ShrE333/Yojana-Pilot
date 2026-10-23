"""Swappable LLM layer. Use get_llm(role); never import a vendor SDK outside llm/providers/."""
from .base import LLMClient, LLMError, LLMResponse
from .config import get_llm

__all__ = ["LLMClient", "LLMError", "LLMResponse", "get_llm"]
