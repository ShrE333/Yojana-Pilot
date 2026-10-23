"""LLM layer: mock provider, JSON validation + retry, role config, logging, provider adapters.

No test here touches the network: vendor providers get fake SDK clients.
"""
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from llm import LLMClient, LLMError, get_llm
from llm.base import parse_json
from llm.config import resolve
from llm.providers.mock_provider import MockProvider

ROOT = Path(__file__).resolve().parent.parent
ENV_VARS = ["LLM_PROVIDER", "LLM_MODEL", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "ANTHROPIC_EFFORT",
            "MOCK_LLM_FILE"] + [f"{r}_{k}" for r in ("EXTRACTOR", "AGENT", "JUDGE") for k in ("PROVIDER", "MODEL")]

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {"age": {"type": "integer", "minimum": 0}, "is_bpl": {"type": "boolean"}},
    "required": ["age"],
    "additionalProperties": False,
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LLM_LOG_PATH", str(tmp_path / "llm_calls.jsonl"))
    return tmp_path / "llm_calls.jsonl"


# ---------- mock provider ----------

def test_mock_matches_by_key_and_satisfies_protocol():
    llm = MockProvider(responses={"extract profile": {"age": 62}, "greet": "namaste"})
    assert isinstance(llm, LLMClient)
    assert llm.name == "mock:mock"
    assert llm.complete([{"role": "user", "content": "please greet"}]).text == "namaste"
    r = llm.complete([{"role": "user", "content": "x"}], system="extract profile", json_schema=PROFILE_SCHEMA)
    assert r.data == {"age": 62}


def test_mock_queue_and_no_match():
    llm = MockProvider(queue=["first", "second"])
    msg = [{"role": "user", "content": "hi"}]
    assert [llm.complete(msg).text, llm.complete(msg).text] == ["first", "second"]
    with pytest.raises(LLMError, match="no canned response"):
        llm.complete(msg)


# ---------- JSON handling ----------

def test_parse_json_tolerates_fences_and_prose():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here it is: {"a": 2} hope it helps') == {"a": 2}


def test_schema_is_added_to_system_prompt():
    llm = MockProvider(queue=[{"age": 30}])
    llm.complete([{"role": "user", "content": "q"}], system="You extract.", json_schema=PROFILE_SCHEMA)
    system = llm.calls[0]["system"]
    assert system.startswith("You extract.") and '"age"' in system
    assert llm.calls[0]["json_mode"] is True


def test_retry_once_on_invalid_json_then_succeed():
    llm = MockProvider(queue=["not json at all", {"age": 45, "is_bpl": True}])
    r = llm.complete([{"role": "user", "content": "q"}], json_schema=PROFILE_SCHEMA)
    assert r.data == {"age": 45, "is_bpl": True}
    assert len(llm.calls) == 2
    retry_msgs = llm.calls[1]["messages"]
    assert retry_msgs[-2] == {"role": "assistant", "content": "not json at all"}
    assert "not valid" in retry_msgs[-1]["content"]


def test_retry_on_schema_violation_names_the_field():
    llm = MockProvider(queue=[{"age": "sixty"}, {"age": 60}])
    r = llm.complete([{"role": "user", "content": "q"}], json_schema=PROFILE_SCHEMA)
    assert r.data == {"age": 60}
    assert "age" in llm.calls[1]["messages"][-1]["content"]


def test_fails_clearly_after_second_invalid_reply():
    llm = MockProvider(queue=[{"age": -1}, {"wrong": True}])
    with pytest.raises(LLMError, match="invalid JSON after retry"):
        llm.complete([{"role": "user", "content": "q"}], json_schema=PROFILE_SCHEMA)
    assert len(llm.calls) == 2


def test_usage_adds_up_across_retry():
    llm = MockProvider(queue=["bad", {"age": 1}])
    r = llm.complete([{"role": "user", "content": "q"}], json_schema=PROFILE_SCHEMA)
    assert r.usage["output_tokens"] > 0 and r.usage["input_tokens"] > 0


# ---------- logging ----------

def test_log_has_metadata_but_no_text(clean_env):
    llm = MockProvider(queue=["bad", {"age": 62}])
    llm.complete([{"role": "user", "content": "Sunil Patil, age 62"}], json_schema=PROFILE_SCHEMA)
    with pytest.raises(LLMError):
        MockProvider(queue=[]).complete([{"role": "user", "content": "Sunil"}])
    lines = [json.loads(l) for l in clean_env.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert lines[0]["ok"] is True and lines[0]["attempts"] == 2 and lines[0]["provider"] == "mock"
    assert lines[1]["ok"] is False and lines[1]["error"]
    raw = clean_env.read_text(encoding="utf-8")
    assert "Sunil" not in raw and "Patil" not in raw


def test_logging_can_be_turned_off(clean_env, monkeypatch):
    monkeypatch.setenv("LLM_LOG", "0")
    MockProvider(queue=["x"]).complete([{"role": "user", "content": "q"}])
    assert not clean_env.exists()


# ---------- role config ----------

def test_default_provider_is_anthropic_with_default_model():
    assert resolve("agent") == ("anthropic", "claude-sonnet-5-5")


def test_llm_provider_and_model_fallback(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "my-model")
    assert resolve("extractor") == ("openai", "my-model")


def test_role_override_and_llm_model_not_leaking_to_other_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("JUDGE_PROVIDER", "gemini")
    assert resolve("judge") == ("gemini", "gemini-3.8-flash")
    monkeypatch.setenv("JUDGE_MODEL", "gemini-x")
    assert resolve("judge") == ("gemini", "gemini-x")
    assert resolve("agent") == ("anthropic", "claude-opus-5-5")


def test_explicit_arguments_win(monkeypatch):
    monkeypatch.setenv("JUDGE_PROVIDER", "gemini")
    assert resolve("judge", provider="openai", model="m") == ("openai", "m")


def test_unknown_role_or_provider():
    with pytest.raises(LLMError, match="role"):
        resolve("writer")
    with pytest.raises(LLMError, match="provider"):
        resolve("agent", provider="cohere")


def test_get_llm_builds_mock_with_kwargs(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    llm = get_llm("extractor", responses={"q": "ok"})
    assert llm.name == "mock:mock" and llm.role == "extractor"
    assert llm.complete([{"role": "user", "content": "q"}]).text == "ok"


def test_mock_reads_responses_file(monkeypatch, tmp_path):
    f = tmp_path / "mock.yaml"
    f.write_text("hello: {age: 5}\n", encoding="utf-8")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_LLM_FILE", str(f))
    assert get_llm("agent").complete([{"role": "user", "content": "hello"}], json_schema=PROFILE_SCHEMA).data == {"age": 5}


def test_judge_same_as_extractor_warns(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    with pytest.warns(UserWarning, match="grading its own work"):
        get_llm("judge")
    monkeypatch.setenv("JUDGE_MODEL", "other-mock")
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        get_llm("judge")


# ---------- vendor adapters with fake SDK clients ----------

class FakeAnthropic:
    def __init__(self, response):
        self.kwargs = None
        create = self._create
        self.messages = NS(create=create)
        self.beta = NS(messages=NS(create=create))
        self.response = response

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _claude_response(stop_reason="end_turn", text='{"age": 70}'):
    return NS(stop_reason=stop_reason, model="claude-sonnet-5-5",
              stop_details=NS(category="cyber") if stop_reason == "refusal" else None,
              content=[NS(type="thinking", thinking=""), NS(type="text", text=text)],
              usage=NS(input_tokens=11, output_tokens=7))


def test_anthropic_adapter_reads_text_blocks_and_uses_fallbacks():
    from llm.providers.anthropic_provider import AnthropicProvider
    fake = FakeAnthropic(_claude_response())
    llm = AnthropicProvider("claude-sonnet-5-5", "extractor", client=fake)
    r = llm.complete([{"role": "user", "content": "q"}], system="sys", json_schema=PROFILE_SCHEMA)
    assert r.data == {"age": 70} and r.usage == {"input_tokens": 11, "output_tokens": 7}
    assert fake.kwargs["fallbacks"] == "default" and "temperature" not in fake.kwargs
    assert fake.kwargs["system"].startswith("sys")


def test_anthropic_adapter_raises_on_refusal():
    from llm.providers.anthropic_provider import AnthropicProvider
    llm = AnthropicProvider("claude-haiku-5-5", client=FakeAnthropic(_claude_response("refusal")))
    with pytest.raises(LLMError, match="refused"):
        llm.complete([{"role": "user", "content": "q"}])


def test_openai_adapter_builds_request(monkeypatch):
    from llm.providers.openai_provider import OpenAIProvider
    seen = {}

    def create(**kw):
        seen.update(kw)
        return NS(model="gpt-test", choices=[NS(message=NS(content='{"age": 33}'))],
                  usage=NS(prompt_tokens=5, completion_tokens=3))

    llm = OpenAIProvider("gpt-test", client=NS(chat=NS(completions=NS(create=create))))
    r = llm.complete([{"role": "user", "content": "q"}], system="sys", json_schema=PROFILE_SCHEMA, max_tokens=50)
    assert r.data == {"age": 33}
    assert seen["messages"][0]["role"] == "system" and seen["response_format"] == {"type": "json_object"}
    assert seen["max_completion_tokens"] == 50 and seen["temperature"] == 0.0

    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    OpenAIProvider("llama3", client=NS(chat=NS(completions=NS(create=create)))).complete(
        [{"role": "user", "content": "q"}], max_tokens=60)
    assert seen["max_tokens"] == 60
    # reasoning models get extra room for hidden reasoning tokens
    OpenAIProvider("openai/gpt-oss-120b", client=NS(chat=NS(completions=NS(create=create)))).complete(
        [{"role": "user", "content": "q"}], max_tokens=60)
    assert seen["max_tokens"] == 4060


def test_gemini_adapter_maps_roles():
    from llm.providers.gemini_provider import GeminiProvider
    seen = {}

    def generate_content(**kw):
        seen.update(kw)
        return NS(text='{"age": 81}', usage_metadata=NS(prompt_token_count=9, candidates_token_count=4))

    llm = GeminiProvider("gemini-test", client=NS(models=NS(generate_content=generate_content)))
    r = llm.complete([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                      {"role": "user", "content": "c"}], json_schema=PROFILE_SCHEMA)
    assert r.data == {"age": 81}
    assert [c.role for c in seen["contents"]] == ["user", "model", "user"]
    assert seen["config"].response_mime_type == "application/json"


def test_vendor_sdk_errors_are_wrapped():
    def boom(**kw):
        raise ConnectionError("network down")
    from llm.providers.openai_provider import OpenAIProvider
    llm = OpenAIProvider("gpt-test", client=NS(chat=NS(completions=NS(create=boom))))
    with pytest.raises(LLMError, match="network down"):
        llm.complete([{"role": "user", "content": "q"}])


# ---------- rule 4: vendor SDKs only inside llm/providers ----------

VENDOR_IMPORT = re.compile(r"^\s*(import|from)\s+(anthropic|openai|google\.genai|google\s+import\s+genai)\b", re.M)


def test_no_vendor_sdk_imports_outside_providers():
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("llm/providers/", ".venv/")):
            continue
        text = path.read_text(encoding="utf-8")
        if VENDOR_IMPORT.search(text) or re.search(r"^\s*from\s+google\s+import\s+genai", text, re.M):
            offenders.append(rel)
    assert offenders == []


# ---------- fallback on capacity errors ----------

class _Failing(MockProvider):
    def __init__(self, message):
        super().__init__(model="primary")
        self.message = message

    def _call(self, *a, **k):
        self.calls.append(1)
        raise LLMError(self.message)


def test_fallback_switches_on_rate_limit_and_stays_switched():
    from llm.fallback import FallbackClient
    primary = _Failing("openai:x: RateLimitError: Error code: 429 - tokens per day")
    secondary = MockProvider(model="backup", responses={"": "ok"})
    client = FallbackClient(primary, secondary, notify=lambda m: None)
    assert client.complete([{"role": "user", "content": "q"}]).text == "ok"
    assert client.complete([{"role": "user", "content": "q"}]).text == "ok"
    assert len(primary.calls) == 1                 # cooldown: primary not retried on the second call
    assert client.name == "mock:backup"


def test_fallback_does_not_hide_other_errors():
    from llm.fallback import FallbackClient
    client = FallbackClient(_Failing("invalid JSON after retry"), MockProvider(responses={"": "ok"}),
                            notify=lambda m: None)
    with pytest.raises(LLMError, match="invalid JSON"):
        client.complete([{"role": "user", "content": "q"}])


def test_get_llm_wraps_fallback_from_env(monkeypatch):
    from llm.fallback import FallbackClient
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "mock")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "backup")
    assert isinstance(get_llm("agent"), FallbackClient)
    assert not isinstance(get_llm("agent", provider="mock"), FallbackClient)   # explicit choice: no wrapper


def test_openai_adapter_retries_without_json_mode_on_server_json_check():
    from llm.providers.openai_provider import OpenAIProvider
    calls = []

    def create(**kw):
        calls.append(kw)
        if "response_format" in kw:
            raise RuntimeError("Error code: 400 - {'code': 'json_validate_failed'}")
        return NS(model="m", choices=[NS(message=NS(content='{"age": 5}'))], usage=NS(prompt_tokens=1, completion_tokens=1))

    llm = OpenAIProvider("m", client=NS(chat=NS(completions=NS(create=create))))
    assert llm.complete([{"role": "user", "content": "q"}], json_schema=PROFILE_SCHEMA).data == {"age": 5}
    assert len(calls) == 2 and "response_format" not in calls[1]



# ---------- local provider, planner role, fallback chain ----------

def test_local_provider_and_planner_role(monkeypatch):
    monkeypatch.setenv("PLANNER_PROVIDER", "local")
    monkeypatch.setenv("PLANNER_MODEL", "nemotron-3-nano")
    assert resolve("planner") == ("local", "nemotron-3-nano")
    from llm.providers.local_provider import LocalProvider
    llm = LocalProvider("nemotron-3-nano", client=NS())
    assert llm.compatible and llm.name == "local:nemotron-3-nano"


def test_fallback_chain_parsing_keeps_colons_in_model_names(monkeypatch):
    from llm.config import resolve_fallbacks
    monkeypatch.setenv("LLM_FALLBACKS", "openai:openai/gpt-oss-20b, local:qwen2.5:7b-instruct")
    assert resolve_fallbacks("agent") == [("openai", "openai/gpt-oss-20b"), ("local", "qwen2.5:7b-instruct")]
    monkeypatch.setenv("PLANNER_FALLBACKS", "local:nemotron-3-nano")
    assert resolve_fallbacks("planner") == [("local", "nemotron-3-nano")]


def test_fallback_chain_walks_to_third_client():
    from llm.fallback import FallbackClient
    a = _Failing("429 rate limit")
    b = _Failing("Connection error.")
    c = MockProvider(model="local", responses={"": "ok"})
    client = FallbackClient(a, b, c, notify=lambda m: None)
    assert client.complete([{"role": "user", "content": "q"}]).text == "ok"
    assert client.name == "mock:local"


def test_busy_provider_is_retried_before_falling_back():
    """A 503 means "loaded right now", so ask the same provider again instead of switching."""
    from llm.fallback import FallbackClient
    calls = []

    class Flaky(MockProvider):
        def _call(self, *a, **k):
            calls.append(1)
            if len(calls) < 3:
                raise LLMError("ServerError: 503 UNAVAILABLE. model is currently experiencing high demand")
            return super()._call(*a, **k)

    primary = Flaky(model="gemini", responses={"": "ok"})
    backup = MockProvider(model="backup", responses={"": "backup"})
    client = FallbackClient(primary, backup, notify=lambda m: None, sleep=lambda s: None)
    assert client.complete([{"role": "user", "content": "q"}]).text == "ok"
    assert len(calls) == 3 and not backup.calls


def test_quota_error_switches_immediately_and_benches_the_provider():
    from llm.fallback import COOLDOWN_SECONDS, FallbackClient
    primary = _Failing("RateLimitError: Error code: 429 tokens per day")
    backup = MockProvider(model="backup", responses={"": "backup"})
    client = FallbackClient(primary, backup, notify=lambda m: None, sleep=lambda s: None)
    assert client.complete([{"role": "user", "content": "q"}]).text == "backup"
    assert len(primary.calls) == 1                      # not retried: asking again cannot help
    assert client._skip_until[0] - time.monotonic() > COOLDOWN_SECONDS - 5
