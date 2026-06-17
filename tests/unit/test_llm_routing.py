"""Unit tests for shared LLM provider routing (infrastructure/llm/routing.py)."""

from glokta.infrastructure.llm.routing import (
    build_request_body,
    resolve_route,
)


class TestResolveRoute:
    def test_openrouter_default(self):
        route = resolve_route("openrouter/anthropic/claude-3.5-sonnet")
        assert route.provider == "openrouter"
        assert route.raw_model == "anthropic/claude-3.5-sonnet"
        assert route.uri == "https://openrouter.ai/api/v1/chat/completions"
        assert route.key_env_var == "OPENROUTER_API_KEY"
        assert route.suppress_thinking is False

    def test_huggingface_route(self):
        route = resolve_route("huggingface/meta-llama/Llama-3.3-70B")
        assert route.provider == "huggingface"
        assert route.raw_model == "meta-llama/Llama-3.3-70B"
        assert route.uri == "https://router.huggingface.co/v1/chat/completions"
        assert route.key_env_var == "HF_TOKEN"

    def test_hf_thinking_model_suppressed(self):
        route = resolve_route("huggingface/Qwen/Qwen3-8B")
        assert route.suppress_thinking is True

    def test_openrouter_thinking_model_not_suppressed(self):
        # Parity with prior behaviour: thinking suppression is HF-only.
        route = resolve_route("openrouter/qwen/qwen3-32b")
        assert route.suppress_thinking is False


class TestBuildRequestBody:
    def test_basic_body(self):
        route = resolve_route("openrouter/x/y")
        body = build_request_body(route, [{"role": "user", "content": "hi"}])
        assert body["model"] == "x/y"
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        assert body["stream"] is False
        assert "thinking" not in body

    def test_thinking_disabled_added_for_suppressed_route(self):
        route = resolve_route("huggingface/Qwen/Qwen3-8B")
        body = build_request_body(route, [{"role": "user", "content": "$INPUT"}])
        assert body["thinking"] == {"type": "disabled"}
