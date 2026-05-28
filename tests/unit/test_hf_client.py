"""Unit tests for the HuggingFace model discovery client."""

import os
from unittest.mock import MagicMock, patch

os.environ["TESTING"] = "1"


def _make_model_info(model_id: str) -> MagicMock:
    m = MagicMock()
    m.id = model_id
    m.modelId = model_id
    return m


_NO_ROUTER_FILTER = patch("glokta.worker.hf_client._fetch_router_model_ids", return_value=None)


class TestFetchTopHfModels:
    def test_returns_list_of_dicts_with_id(self):
        """Each returned dict has an 'id' key with the bare HF model ID."""
        from glokta.worker.hf_client import fetch_top_hf_models

        fake_models = [
            _make_model_info("meta-llama/Llama-3.1-8B-Instruct"),
            _make_model_info("mistralai/Mistral-7B-Instruct-v0.3"),
        ]

        with patch("glokta.worker.hf_client.HfApi") as MockApi, _NO_ROUTER_FILTER:
            MockApi.return_value.list_models.return_value = iter(fake_models)
            result = fetch_top_hf_models(hf_token="test-token", top_n=2)

        assert len(result) == 2
        assert result[0]["id"] == "meta-llama/Llama-3.1-8B-Instruct"
        assert result[1]["id"] == "mistralai/Mistral-7B-Instruct-v0.3"

    def test_id_has_no_huggingface_prefix(self):
        """Returned ids are bare HF model IDs, not prefixed with 'huggingface/'."""
        from glokta.worker.hf_client import fetch_top_hf_models

        fake = [_make_model_info("org/model")]
        with patch("glokta.worker.hf_client.HfApi") as MockApi, _NO_ROUTER_FILTER:
            MockApi.return_value.list_models.return_value = iter(fake)
            result = fetch_top_hf_models(hf_token="tok", top_n=1)

        assert not result[0]["id"].startswith("huggingface/")

    def test_passes_correct_list_models_args(self):
        """list_models is called with overfetch limit (max(top_n*10, 200)) — not top_n."""
        from glokta.worker.hf_client import fetch_top_hf_models

        top_n = 5
        with patch("glokta.worker.hf_client.HfApi") as MockApi, _NO_ROUTER_FILTER:
            MockApi.return_value.list_models.return_value = iter([])
            fetch_top_hf_models(hf_token="tok", top_n=top_n)

        MockApi.return_value.list_models.assert_called_once_with(
            filter="text-generation",
            inference="warm",
            sort="downloads",
            limit=max(top_n * 10, 200),
        )

    def test_empty_list_on_no_results(self):
        """Returns empty list when HfApi yields no models."""
        from glokta.worker.hf_client import fetch_top_hf_models

        with patch("glokta.worker.hf_client.HfApi") as MockApi, _NO_ROUTER_FILTER:
            MockApi.return_value.list_models.return_value = iter([])
            result = fetch_top_hf_models(hf_token="tok", top_n=10)

        assert result == []

    def test_skips_models_with_no_id(self):
        """Models returned by HfApi with no id attribute are silently skipped."""
        from glokta.worker.hf_client import fetch_top_hf_models

        no_id = MagicMock()
        no_id.id = None
        no_id.modelId = None
        valid = _make_model_info("org/valid-model")

        with patch("glokta.worker.hf_client.HfApi") as MockApi, _NO_ROUTER_FILTER:
            MockApi.return_value.list_models.return_value = iter([no_id, valid])
            result = fetch_top_hf_models(hf_token="tok", top_n=5)

        assert len(result) == 1
        assert result[0]["id"] == "org/valid-model"

    def test_hfapi_constructed_with_token(self):
        """HfApi is constructed with the provided token."""
        from glokta.worker.hf_client import fetch_top_hf_models

        with patch("glokta.worker.hf_client.HfApi") as MockApi, _NO_ROUTER_FILTER:
            MockApi.return_value.list_models.return_value = iter([])
            fetch_top_hf_models(hf_token="my-token", top_n=3)

        MockApi.assert_called_once_with(token="my-token")


class TestFetchTopHfModelsRouterFilter:
    """fetch_top_hf_models cross-filters Hub results against the HF Router's model list."""

    def _router_resp(self, model_ids: list[str]) -> MagicMock:
        resp = MagicMock()
        resp.json.return_value = {"data": [{"id": mid} for mid in model_ids]}
        return resp

    def test_excludes_model_not_in_router(self):
        """Models absent from the HF Router /v1/models list are filtered out."""
        from glokta.worker.hf_client import fetch_top_hf_models

        hub_models = [
            _make_model_info("org/in-router"),
            _make_model_info("org/not-in-router"),
        ]
        with patch("glokta.worker.hf_client.HfApi") as MockApi, \
             patch("glokta.worker.hf_client.requests") as mock_req:
            MockApi.return_value.list_models.return_value = iter(hub_models)
            mock_req.get.return_value = self._router_resp(["org/in-router"])
            result = fetch_top_hf_models(hf_token="tok", top_n=10)

        ids = [r["id"] for r in result]
        assert "org/in-router" in ids
        assert "org/not-in-router" not in ids

    def test_includes_model_present_in_router(self):
        """Models in the HF Router model list pass through unchanged."""
        from glokta.worker.hf_client import fetch_top_hf_models

        hub_models = [_make_model_info("meta-llama/Llama-3.1-8B-Instruct")]
        with patch("glokta.worker.hf_client.HfApi") as MockApi, \
             patch("glokta.worker.hf_client.requests") as mock_req:
            MockApi.return_value.list_models.return_value = iter(hub_models)
            mock_req.get.return_value = self._router_resp(["meta-llama/Llama-3.1-8B-Instruct"])
            result = fetch_top_hf_models(hf_token="tok", top_n=10)

        assert result == [{"id": "meta-llama/Llama-3.1-8B-Instruct"}]

    def test_router_called_with_bearer_token(self):
        """Router /v1/models is called with Authorization: Bearer <token>."""
        from glokta.worker.hf_client import fetch_top_hf_models, _HF_ROUTER_MODELS_URL

        with patch("glokta.worker.hf_client.HfApi") as MockApi, \
             patch("glokta.worker.hf_client.requests") as mock_req:
            MockApi.return_value.list_models.return_value = iter([])
            mock_req.get.return_value = self._router_resp([])
            fetch_top_hf_models(hf_token="my-hf-token", top_n=5)

        mock_req.get.assert_called_once_with(
            _HF_ROUTER_MODELS_URL,
            headers={"Authorization": "Bearer my-hf-token"},
            timeout=15,
        )

    def test_graceful_degradation_on_router_error(self):
        """If the Router call raises, results are returned unfiltered — no crash."""
        from glokta.worker.hf_client import fetch_top_hf_models

        hub_models = [_make_model_info("org/model-a"), _make_model_info("org/model-b")]
        with patch("glokta.worker.hf_client.HfApi") as MockApi, \
             patch("glokta.worker.hf_client.requests") as mock_req:
            MockApi.return_value.list_models.return_value = iter(hub_models)
            mock_req.get.side_effect = Exception("network error")
            result = fetch_top_hf_models(hf_token="tok", top_n=10)

        assert len(result) == 2
        assert {r["id"] for r in result} == {"org/model-a", "org/model-b"}
