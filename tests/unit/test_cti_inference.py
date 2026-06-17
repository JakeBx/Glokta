"""Unit tests for the CTI direct chat-completions client.

No live network: a fake httpx-style client captures the request and returns canned
responses, so we assert request shaping (routing, messages, auth) and response parsing.
"""

import pytest

from glokta.infrastructure.cti.inference import (
    InferenceError,
    complete,
    complete_via_openrouter,
)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """Captures the last POST and returns a queued response."""

    def __init__(self, response: FakeResponse):
        self._response = response
        self.calls: list[dict] = []

    def post(self, url: str, json: dict, headers: dict):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self._response


def _ok(content: str) -> FakeResponse:
    return FakeResponse({"choices": [{"message": {"content": content}}]})


class TestComplete:
    def test_returns_message_content(self):
        client = FakeClient(_ok("CWE-79"))
        out = complete("openrouter/x/y", "classify this", api_key="k", client=client)
        assert out == "CWE-79"

    def test_routes_to_openrouter_with_auth_and_model(self):
        client = FakeClient(_ok("ok"))
        complete("openrouter/anthropic/claude", "p", api_key="secret", client=client)
        call = client.calls[0]
        assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
        assert call["headers"]["Authorization"] == "Bearer secret"
        assert call["json"]["model"] == "anthropic/claude"

    def test_user_prompt_in_messages(self):
        client = FakeClient(_ok("ok"))
        complete("openrouter/x/y", "the prompt", api_key="k", client=client)
        messages = client.calls[0]["json"]["messages"]
        assert messages[-1] == {"role": "user", "content": "the prompt"}

    def test_system_prompt_prepended(self):
        client = FakeClient(_ok("ok"))
        complete("openrouter/x/y", "p", system="be terse", api_key="k", client=client)
        messages = client.calls[0]["json"]["messages"]
        assert messages[0] == {"role": "system", "content": "be terse"}

    def test_temperature_defaults_to_zero(self):
        client = FakeClient(_ok("ok"))
        complete("openrouter/x/y", "p", api_key="k", client=client)
        assert client.calls[0]["json"]["temperature"] == 0.0

    def test_hf_thinking_model_disables_thinking(self):
        client = FakeClient(_ok("ok"))
        complete("huggingface/Qwen/Qwen3-8B", "p", api_key="k", client=client)
        assert client.calls[0]["json"]["thinking"] == {"type": "disabled"}

    def test_malformed_response_raises(self):
        client = FakeClient(FakeResponse({"unexpected": True}))
        with pytest.raises(InferenceError):
            complete("openrouter/x/y", "p", api_key="k", client=client)

    def test_empty_choices_raises(self):
        client = FakeClient(FakeResponse({"choices": []}))
        with pytest.raises(InferenceError):
            complete("openrouter/x/y", "p", api_key="k", client=client)


class TestForceOpenRouter:
    """The CTI judge must always route via OpenRouter, regardless of how it is named."""

    def test_provider_override_forces_openrouter_for_hf_named_model(self):
        client = FakeClient(_ok("YES"))
        complete(
            "huggingface/meta-llama/Llama", "p",
            api_key="k", client=client, provider="openrouter",
        )
        call = client.calls[0]
        assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
        # huggingface/ prefix stripped; no HF thinking flag on the OpenRouter route
        assert call["json"]["model"] == "meta-llama/Llama"
        assert "thinking" not in call["json"]

    def test_complete_via_openrouter_routes_bare_anthropic_slug(self):
        client = FakeClient(_ok("NO"))
        complete_via_openrouter(
            "anthropic/claude-opus-4.8", "is this grounded?", api_key="k", client=client
        )
        call = client.calls[0]
        assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
        assert call["json"]["model"] == "anthropic/claude-opus-4.8"

    def test_default_judge_model_is_a_valid_openrouter_slug(self):
        from glokta.config import settings

        assert "/" in settings.cti_judge_model  # vendor-prefixed, not a bare Anthropic id


class TestErrorResponses:
    """A non-2xx / non-JSON provider reply must surface as a clean InferenceError."""

    def test_persistent_5xx_raises_inference_error_not_jsondecode(self):
        # 504 gateway timeout after retries — must NOT crash on resp.json()
        client = FakeClient(FakeResponse({}, status_code=504))
        with pytest.raises(InferenceError) as ei:
            complete("openrouter/x/y", "p", api_key="k", client=client, max_retries=0)
        assert "504" in str(ei.value)

    def test_non_json_2xx_body_raises_inference_error(self):
        class _BadJson:
            status_code = 200
            text = "<html>gateway timeout</html>"

            def json(self):
                raise ValueError("no json here")  # what httpx raises on a non-JSON body

        client = FakeClient(_BadJson())
        with pytest.raises(InferenceError):
            complete("openrouter/x/y", "p", api_key="k", client=client, max_retries=0)
