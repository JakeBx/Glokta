"""Unit tests for the HuggingFace model discovery client."""

import os
from unittest.mock import MagicMock, patch

os.environ["TESTING"] = "1"


def _make_model_info(model_id: str) -> MagicMock:
    m = MagicMock()
    m.id = model_id
    m.modelId = model_id
    return m


class TestFetchTopHfModels:
    def test_returns_list_of_dicts_with_id(self):
        """Each returned dict has an 'id' key with the bare HF model ID."""
        from glokta.worker.hf_client import fetch_top_hf_models

        fake_models = [
            _make_model_info("meta-llama/Llama-3.1-8B-Instruct"),
            _make_model_info("mistralai/Mistral-7B-Instruct-v0.3"),
        ]

        with patch("glokta.worker.hf_client.HfApi") as MockApi:
            MockApi.return_value.list_models.return_value = iter(fake_models)
            result = fetch_top_hf_models(hf_token="test-token", top_n=2)

        assert len(result) == 2
        assert result[0]["id"] == "meta-llama/Llama-3.1-8B-Instruct"
        assert result[1]["id"] == "mistralai/Mistral-7B-Instruct-v0.3"

    def test_id_has_no_huggingface_prefix(self):
        """Returned ids are bare HF model IDs, not prefixed with 'huggingface/'."""
        from glokta.worker.hf_client import fetch_top_hf_models

        fake = [_make_model_info("org/model")]
        with patch("glokta.worker.hf_client.HfApi") as MockApi:
            MockApi.return_value.list_models.return_value = iter(fake)
            result = fetch_top_hf_models(hf_token="tok", top_n=1)

        assert not result[0]["id"].startswith("huggingface/")

    def test_passes_correct_list_models_args(self):
        """list_models is called with filter=text-generation, inference=warm, sort=downloads."""
        from glokta.worker.hf_client import fetch_top_hf_models

        with patch("glokta.worker.hf_client.HfApi") as MockApi:
            MockApi.return_value.list_models.return_value = iter([])
            fetch_top_hf_models(hf_token="tok", top_n=5)

        MockApi.return_value.list_models.assert_called_once_with(
            filter="text-generation",
            inference="warm",
            sort="downloads",
            limit=5,
        )

    def test_empty_list_on_no_results(self):
        """Returns empty list when HfApi yields no models."""
        from glokta.worker.hf_client import fetch_top_hf_models

        with patch("glokta.worker.hf_client.HfApi") as MockApi:
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

        with patch("glokta.worker.hf_client.HfApi") as MockApi:
            MockApi.return_value.list_models.return_value = iter([no_id, valid])
            result = fetch_top_hf_models(hf_token="tok", top_n=5)

        assert len(result) == 1
        assert result[0]["id"] == "org/valid-model"

    def test_hfapi_constructed_with_token(self):
        """HfApi is constructed with the provided token."""
        from glokta.worker.hf_client import fetch_top_hf_models

        with patch("glokta.worker.hf_client.HfApi") as MockApi:
            MockApi.return_value.list_models.return_value = iter([])
            fetch_top_hf_models(hf_token="my-token", top_n=3)

        MockApi.assert_called_once_with(token="my-token")
