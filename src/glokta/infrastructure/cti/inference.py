"""Direct single-shot chat-completions client for CTI evaluation.

Unlike the garak path (which drives inference via garak's RestGenerator subprocess),
CTI tasks need one prompt -> one completion. This client reuses the shared provider
routing so OpenRouter/HF endpoints, keys and thinking-suppression stay defined once.
"""

import logging
import time
from typing import Any, Protocol

import httpx

from glokta.infrastructure.llm.routing import build_request_body, resolve_route

logger = logging.getLogger(__name__)

# HTTP statuses worth retrying: rate limiting and transient server errors.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class InferenceError(Exception):
    """Raised when the provider response cannot be parsed into message content."""


class _HttpClient(Protocol):
    def post(self, url: str, json: dict, headers: dict) -> Any: ...


def _default_key(key_env_var: str) -> str:
    """Resolve the provider API key from settings for the route's env var."""
    from glokta.config import settings

    if key_env_var == "HF_TOKEN":
        return settings.hf_token
    return settings.openrouter_api_key


def _extract_content(data: dict) -> str:
    """Pull message content out of an OpenAI-compatible chat-completions response."""
    try:
        choices = data["choices"]
        content = choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise InferenceError(f"Unexpected completions response shape: {data!r}") from exc
    if content is None:
        raise InferenceError(f"Null message content in response: {data!r}")
    return content


def _post_with_retry(
    client: Any,
    url: str,
    body: dict,
    headers: dict,
    max_retries: int,
    backoff_seconds: float,
) -> Any:
    """POST with simple retry on rate-limit / transient server errors."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.post(url, json=body, headers=headers)
            if resp.status_code in _RETRYABLE_STATUS and attempt < max_retries:
                logger.warning(
                    "inference: retryable status %s (attempt %d/%d)",
                    resp.status_code,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(backoff_seconds * (2**attempt))
                continue
            return resp
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            logger.warning("inference: transport error %s; retrying", exc)
            time.sleep(backoff_seconds * (2**attempt))
    raise InferenceError(f"Inference request failed after retries: {last_exc}")


def complete(
    model_name: str,
    prompt: str,
    *,
    system: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    timeout: float | None = None,
    max_retries: int = 3,
    backoff_seconds: float = 1.0,
    client: _HttpClient | None = None,
    provider: str | None = None,
) -> str:
    """Run one chat-completion and return the message content.

    ``client`` may be injected (tests / connection reuse); otherwise a short-lived
    ``httpx.Client`` is created and closed. ``api_key`` defaults to the settings value
    for the resolved provider. ``provider="openrouter"`` pins the OpenRouter route regardless
    of the model name's source prefix (used for the CTI judge).
    """
    route = resolve_route(model_name, force_provider=provider)
    key = api_key if api_key is not None else _default_key(route.key_env_var)

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = build_request_body(route, messages, stream=False)
    body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    request_timeout = timeout if timeout is not None else float(route.request_timeout)

    owns_client = client is None
    active: Any = client if client is not None else httpx.Client(timeout=request_timeout)
    try:
        resp = _post_with_retry(
            active, route.uri, body, headers, max_retries, backoff_seconds
        )
    finally:
        if owns_client:
            active.close()

    # A non-2xx reply (e.g. a 504 that outlived its retries) or a non-JSON body must surface as a
    # clean InferenceError — not an opaque JSONDecodeError from resp.json() that aborts the caller.
    if resp.status_code >= 400:
        snippet = (getattr(resp, "text", "") or "")[:200]
        raise InferenceError(
            f"Provider returned HTTP {resp.status_code} for {route.raw_model!r}: {snippet!r}"
        )
    try:
        data = resp.json()
    except ValueError as exc:  # json.JSONDecodeError is a ValueError subclass
        snippet = (getattr(resp, "text", "") or "")[:200]
        raise InferenceError(
            f"Non-JSON response (HTTP {resp.status_code}) for {route.raw_model!r}: {snippet!r}"
        ) from exc
    return _extract_content(data)


def complete_via_openrouter(model_name: str, prompt: str, **kwargs: Any) -> str:
    """Run a completion forcing the OpenRouter provider.

    Used for the CTI judge so the judge model (``cti_judge_model``) always routes through the
    OpenRouter client and key, independent of the model-under-test's provider.
    """
    return complete(model_name, prompt, provider="openrouter", **kwargs)
