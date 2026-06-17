"""Provider routing for glokta model names.

glokta model names are ``"<source>/<id...>"`` — e.g. ``openrouter/anthropic/claude-3.5``
or ``huggingface/meta-llama/Llama-3.3-70B``. Both the garak config builder and the CTI
inference client need the same per-provider facts (endpoint URI, API-key env var, request
timeout, and whether to suppress thinking/reasoning mode). This module is the single source
of truth so those rules are defined once.
"""

from dataclasses import dataclass

_OPENROUTER_GENERATOR_NAME = "openrouter-direct"
_HF_GENERATOR_NAME = "hf-inference-direct"
_OPENROUTER_URI = "https://openrouter.ai/api/v1/chat/completions"
# HF Inference Providers router — model identified via the "model" field in the request body
_HF_URI = "https://router.huggingface.co/v1/chat/completions"

# Per-provider request timeouts. HF serverless inference is slower, especially for large
# models — 180s gives breathing room for thinking-mode models that stream a long reasoning
# preamble before the answer.
_HF_REQUEST_TIMEOUT = 180
_OPENROUTER_REQUEST_TIMEOUT = 90

# Model name substrings that indicate thinking/reasoning mode is on by default. For these we
# inject the provider flag to disable thinking so responses fit the buffer, latency stays
# under the timeout, and content lands in message.content (not reasoning_content).
_THINKING_MODEL_SUBSTRINGS = (
    "qwen3",          # Qwen3-* default to thinking mode on HF serverless
    "deepseek-r",     # DeepSeek-R series
    "deepseek-v4",    # DeepSeek-V4-Pro uses extended reasoning
    "kimi-k2",        # Moonshot Kimi K2
    "kimi_k2",
)


def is_thinking_model(raw_model: str) -> bool:
    """Return True if the model name suggests it defaults to thinking/reasoning mode."""
    lower = raw_model.lower()
    return any(sub in lower for sub in _THINKING_MODEL_SUBSTRINGS)


@dataclass(frozen=True)
class ProviderRoute:
    """Resolved routing facts for a glokta model name."""

    provider: str          # "openrouter" | "huggingface"
    raw_model: str         # model id with the source prefix stripped
    uri: str
    key_env_var: str
    generator_name: str
    request_timeout: int
    suppress_thinking: bool  # disable thinking mode for this model on this provider


def _openrouter_route(raw_model: str) -> ProviderRoute:
    return ProviderRoute(
        provider="openrouter",
        raw_model=raw_model,
        uri=_OPENROUTER_URI,
        key_env_var="OPENROUTER_API_KEY",
        generator_name=_OPENROUTER_GENERATOR_NAME,
        request_timeout=_OPENROUTER_REQUEST_TIMEOUT,
        suppress_thinking=False,
    )


def resolve_route(model_name: str, *, force_provider: str | None = None) -> ProviderRoute:
    """Resolve the provider route for a ``"<source>/<id>"`` glokta model name.

    Anything not prefixed ``huggingface/`` routes to OpenRouter (the historical default).
    ``force_provider="openrouter"`` pins the OpenRouter route regardless of the name's prefix
    (used for the CTI judge, which must always go through OpenRouter); any ``huggingface/`` or
    ``openrouter/`` source prefix is stripped from the model id.
    Thinking suppression is applied for HF serverless only, matching prior garak behaviour.
    """
    if force_provider == "openrouter":
        raw_model = model_name.removeprefix("huggingface/").removeprefix("openrouter/")
        return _openrouter_route(raw_model)

    if model_name.startswith("huggingface/"):
        raw_model = model_name.removeprefix("huggingface/")
        return ProviderRoute(
            provider="huggingface",
            raw_model=raw_model,
            uri=_HF_URI,
            key_env_var="HF_TOKEN",
            generator_name=_HF_GENERATOR_NAME,
            request_timeout=_HF_REQUEST_TIMEOUT,
            suppress_thinking=is_thinking_model(raw_model),
        )

    return _openrouter_route(model_name.removeprefix("openrouter/"))


def build_request_body(
    route: ProviderRoute,
    messages: list[dict],
    stream: bool = False,
) -> dict:
    """Build the chat-completions request body for a route.

    ``messages`` may contain garak placeholders (``"$INPUT"``) or real content. When the
    route suppresses thinking, the provider's ``{"thinking": {"type": "disabled"}}`` flag
    is added.
    """
    body: dict = {"model": route.raw_model, "messages": messages, "stream": stream}
    if route.suppress_thinking:
        body["thinking"] = {"type": "disabled"}
    return body
