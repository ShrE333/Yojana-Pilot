"""Google Gemini via the google-genai SDK."""
from __future__ import annotations

import os

from google import genai
from google.genai import types

from ..base import ProviderBase, RawResult

# current Gemini models think by default, and thinking tokens count toward max_output_tokens
THINKING_HEADROOM = 4000


class GeminiProvider(ProviderBase):
    provider = "gemini"

    def __init__(self, model: str, role: str = "default", client=None):
        super().__init__(model, role)
        self.client = client or genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    def _call(self, messages, system, json_mode, temperature, max_tokens) -> RawResult:
        contents = [types.Content(role="model" if m["role"] == "assistant" else "user",
                                  parts=[types.Part(text=m["content"])])
                    for m in messages]
        config = types.GenerateContentConfig(
            system_instruction=system, temperature=temperature, max_output_tokens=max_tokens + THINKING_HEADROOM,
            response_mime_type="application/json" if json_mode else None)
        resp = self.client.models.generate_content(model=self.model, contents=contents, config=config)
        um = resp.usage_metadata
        return RawResult(text=resp.text or "", model=self.model,
                         usage={"input_tokens": getattr(um, "prompt_token_count", 0) or 0,
                                "output_tokens": getattr(um, "candidates_token_count", 0) or 0})
