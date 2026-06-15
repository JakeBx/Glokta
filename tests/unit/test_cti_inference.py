"""Unit tests for the CTI direct chat-completions client.

No live network: a fake httpx-style client captures the request and returns canned
responses, so we assert request shaping (routing, messages, auth) and response parsing.
"""

import pytest

from glokta.infrastructure.cti.inference import InferenceError, complete


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
