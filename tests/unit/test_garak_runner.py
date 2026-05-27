"""Unit tests for garak_runner — build_garak_config and run_garak."""

import os

os.environ["TESTING"] = "1"


class TestBuildGarakConfigHf:
    """build_garak_config HF dispatch — huggingface/ prefix produces HF-specific config."""

    def _hf_config(self, model_id: str = "meta-llama/Llama-3.1-8B-Instruct") -> dict:
        from glokta.worker.garak_runner import build_garak_config
        return build_garak_config(
            model_name=f"huggingface/{model_id}",
            probe_categories=["dan"],
            output_dir="/tmp/out",
        )

    def test_hf_uri_points_to_hf_router(self):
        """HF config URI points to router.huggingface.co (not api-inference subdomain)."""
        cfg = self._hf_config()
        uri = cfg["plugins"]["generators"]["rest"]["RestGenerator"]["uri"]
        assert "router.huggingface.co" in uri

    def test_hf_key_env_var_is_hf_token(self):
        """HF config sets key_env_var to HF_TOKEN, not OPENROUTER_API_KEY."""
        cfg = self._hf_config()
        key_var = cfg["plugins"]["generators"]["rest"]["RestGenerator"]["key_env_var"]
        assert key_var == "HF_TOKEN"

    def test_hf_request_body_has_model_field(self):
        """HF request body includes the model field (router endpoint uses it, not the URL path)."""
        cfg = self._hf_config()
        body = cfg["plugins"]["generators"]["rest"]["RestGenerator"]["req_template_json_object"]
        assert body["model"] == "meta-llama/Llama-3.1-8B-Instruct"

    def test_hf_request_body_has_messages(self):
        """HF request body includes the messages array."""
        cfg = self._hf_config()
        body = cfg["plugins"]["generators"]["rest"]["RestGenerator"]["req_template_json_object"]
        assert "messages" in body

    def test_hf_generator_name_differs_from_openrouter(self):
        """HF and OpenRouter configs use distinct generator names to avoid garak cache collisions."""
        from glokta.worker.garak_runner import build_garak_config
        hf_cfg = build_garak_config("huggingface/org/model", ["dan"], "/tmp/out")
        or_cfg = build_garak_config("openrouter/org/model", ["dan"], "/tmp/out")
        hf_name = hf_cfg["plugins"]["generators"]["rest"]["RestGenerator"]["name"]
        or_name = or_cfg["plugins"]["generators"]["rest"]["RestGenerator"]["name"]
        assert hf_name != or_name


class TestBuildGarakConfigOpenRouter:
    """Regression tests: OpenRouter behavior must be unchanged after adding HF dispatch."""

    def test_openrouter_uri_unchanged(self):
        """OpenRouter config still points to openrouter.ai endpoint."""
        from glokta.worker.garak_runner import build_garak_config
        cfg = build_garak_config("openrouter/meta-llama/llama-3-8b-instruct:free", ["dan"], "/tmp/out")
        uri = cfg["plugins"]["generators"]["rest"]["RestGenerator"]["uri"]
        assert "openrouter.ai" in uri

    def test_openrouter_key_env_var_unchanged(self):
        """OpenRouter config still uses OPENROUTER_API_KEY."""
        from glokta.worker.garak_runner import build_garak_config
        cfg = build_garak_config("openrouter/org/model", ["dan"], "/tmp/out")
        key_var = cfg["plugins"]["generators"]["rest"]["RestGenerator"]["key_env_var"]
        assert key_var == "OPENROUTER_API_KEY"

    def test_openrouter_request_body_has_model_field(self):
        """OpenRouter request body still includes the model field."""
        from glokta.worker.garak_runner import build_garak_config
        cfg = build_garak_config("openrouter/org/model", ["dan"], "/tmp/out")
        body = cfg["plugins"]["generators"]["rest"]["RestGenerator"]["req_template_json_object"]
        assert "model" in body


class TestRunGarakEnvOverrides:
    """run_garak should accept env_overrides dict instead of api_key str."""

    def test_accepts_env_overrides_dict(self, tmp_path):
        """run_garak accepts env_overrides: dict[str, str] parameter."""
        import subprocess
        from unittest.mock import patch, MagicMock

        from glokta.worker.garak_runner import run_garak

        fake_jsonl = tmp_path / "scan.report.jsonl"
        fake_jsonl.write_text('{"key": "value"}\n')

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = iter([])
        fake_proc.stderr = iter([])
        fake_proc.wait.return_value = 0

        config = {"reporting": {"report_dir": str(tmp_path)}, "plugins": {}}

        with patch("subprocess.Popen", return_value=fake_proc):
            # Should not raise TypeError — env_overrides is the new parameter name
            run_garak(config, env_overrides={"HF_TOKEN": "my-hf-token"}, timeout=5)

    def test_env_overrides_injected_into_subprocess_env(self, tmp_path):
        """env_overrides dict is merged into the subprocess environment."""
        import subprocess
        from unittest.mock import patch, MagicMock, call

        from glokta.worker.garak_runner import run_garak

        fake_jsonl = tmp_path / "scan.report.jsonl"
        fake_jsonl.write_text('{"key": "value"}\n')

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = iter([])
        fake_proc.stderr = iter([])
        fake_proc.wait.return_value = 0

        config = {"reporting": {"report_dir": str(tmp_path)}, "plugins": {}}

        captured_env = {}

        def capture_popen(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return fake_proc

        with patch("subprocess.Popen", side_effect=capture_popen):
            run_garak(config, env_overrides={"HF_TOKEN": "secret-tok"}, timeout=5)

        assert captured_env.get("HF_TOKEN") == "secret-tok"
