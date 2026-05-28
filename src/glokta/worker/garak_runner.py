"""Utilities for spawning and managing garak subprocess runs."""

import functools
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Default timeout — overridden by settings.garak_timeout_seconds at call sites.
GARAK_TIMEOUT_SECONDS = 3600

DEFAULT_PROBE_CATEGORIES = [
    "ansiescape",           # Terminal Manipulation / Log Poisoning
    "apikey",               # Synthetic Credential Generation
    "av_spam_scanning",     # Missing Output Security Controls
    "exploitation",         # Code Injection (SQLi, SSTI, RCE)
    "malwaregen",           # Weaponization of Code Generation
    "packagehallucination", # Supply Chain Poisoning
    "promptinject",         # Prompt Injection / String Hijacking
    "sysprompt_extraction", # Information Disclosure / Reconnaissance
    "web_injection",        # XSS, CSRF, and Data Exfiltration
]


@functools.cache
def _all_garak_probe_names() -> tuple[str, ...]:
    """Return all installed garak probe full names. Cached after first call."""
    from garak import _plugins
    return tuple(name for name, _ in _plugins.enumerate_plugins("probes"))


def compute_remaining_probes(done: set[str], probe_categories: list[str]) -> list[str]:
    """Return probe names not yet represented in done, in the short form garak probe_spec expects.

    done: set of DB probe_name values e.g. {"encoding.InjectBase64", "dan.Dan_11_0"}
    Returns short "encoding.InjectBase64" style names (no "probes." prefix) — this is
    the format garak's probe_spec config key accepts.

    Probes whose class name ends with "Full" are excluded: they run all their
    prompts without respecting soft_probe_prompt_cap and make scans prohibitively
    long at API rate limits.
    """
    category_set = set(probe_categories)
    result = []
    for full_name in _all_garak_probe_names():
        db_name = full_name.removeprefix("probes.")
        probe_class = db_name.split(".")[-1] if "." in db_name else db_name
        category = db_name.split(".")[0]
        if category in category_set and db_name not in done and not probe_class.endswith("Full"):
            result.append(db_name)
    return result


_OPENROUTER_GENERATOR_NAME = "openrouter-direct"
_HF_GENERATOR_NAME = "hf-inference-direct"
_OPENROUTER_URI = "https://openrouter.ai/api/v1/chat/completions"
# HF Inference Providers router — model identified via the "model" field in the request body
_HF_URI = "https://router.huggingface.co/v1/chat/completions"


def build_garak_config(
    model_name: str,
    probe_categories: list[str],
    output_dir: str,
    parallel_attempts: int = 1,
    rpm_limit: int | None = None,
    soft_probe_prompt_cap: int | None = None,
    probe_spec_override: str | None = None,
) -> dict:
    """
    Build a garak configuration dict suitable for writing as YAML.

    Args:
        model_name: Model name with provider prefix, e.g.
            'openrouter/meta-llama/llama-3-8b-instruct:free' or
            'huggingface/meta-llama/Llama-3.1-8B-Instruct'
        probe_categories: List of probe category names e.g. ['encoding', 'malwaregen']
        output_dir: Directory where garak should write its JSONL output
        parallel_attempts: Number of parallel attempts (default 1)
        rpm_limit: Optional rate limit in requests per minute for the generator
        soft_probe_prompt_cap: Optional limit on prompts per probe (default None)

    Returns:
        dict suitable for yaml.dump()
    """
    probes = probe_categories if probe_categories else DEFAULT_PROBE_CATEGORIES
    spec = probe_spec_override if probe_spec_override is not None else ",".join(probes)

    if model_name.startswith("huggingface/"):
        raw_model = model_name.removeprefix("huggingface/")
        generator_name = _HF_GENERATOR_NAME
        uri = _HF_URI
        key_env_var = "HF_TOKEN"
        req_body: dict = {
            "model": raw_model,
            "messages": [{"role": "user", "content": "$INPUT"}],
            "stream": False,
        }
    else:
        raw_model = model_name.removeprefix("openrouter/")
        generator_name = _OPENROUTER_GENERATOR_NAME
        uri = _OPENROUTER_URI
        key_env_var = "OPENROUTER_API_KEY"
        req_body = {
            "model": raw_model,
            "messages": [{"role": "user", "content": "$INPUT"}],
            "stream": False,
        }

    config: dict = {
        "system": {
            "parallel_attempts": parallel_attempts,
        },
        "plugins": {
            "target_type": "rest",
            "target_name": generator_name,
            "probe_spec": spec,
            "generators": {
                "rest": {
                    "RestGenerator": {
                        "name": generator_name,
                        "uri": uri,
                        "method": "post",
                        "headers": {
                            "Content-Type": "application/json",
                            "Authorization": "Bearer $KEY",
                        },
                        "key_env_var": key_env_var,
                        "req_template_json_object": req_body,
                        "response_json": True,
                        "response_json_field": "$.choices[0].message.content",
                        "request_timeout": 60,
                    }
                }
            },
        },
        "reporting": {
            "report_dir": output_dir,
        },
    }

    if rpm_limit is not None:
        config["system"]["generators_options"] = {
            "max_requests_per_minute": rpm_limit,
        }

    if soft_probe_prompt_cap is not None:
        config["run"] = {
            "soft_probe_prompt_cap": soft_probe_prompt_cap,
            "generations": 1,
        }

    return config


def run_garak(
    config: dict,
    env_overrides: dict[str, str],
    timeout: int = GARAK_TIMEOUT_SECONDS,
    pf_logger: logging.Logger | None = None,
) -> str:
    """
    Write a garak YAML config and run garak as a subprocess.

    Args:
        config: garak config dict from build_garak_config()
        env_overrides: Environment variables to inject (e.g. {"OPENROUTER_API_KEY": "..."}
            for OpenRouter or {"HF_TOKEN": "..."} for HuggingFace).

    Returns:
        Path to the garak JSONL output file

    Raises:
        subprocess.CalledProcessError: If garak exits with non-zero status
        subprocess.TimeoutExpired: If garak does not complete within GARAK_TIMEOUT_SECONDS
        FileNotFoundError: If the expected JSONL output file is not found after run
    """
    output_dir = config["reporting"]["report_dir"]

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as config_file:
        yaml.dump(config, config_file)
        config_path = config_file.name

    _log = pf_logger or logger
    try:
        env = os.environ.copy()
        env.update(env_overrides)

        proc = subprocess.Popen(
            ["garak", "--config", config_path],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        def _drain(stream: object, label: str) -> None:
            try:
                for line in stream:  # type: ignore[union-attr]
                    _log.info("[garak %s] %s", label, line.rstrip())
            except ValueError:
                pass  # stream closed before we finished reading

        t_out = threading.Thread(target=_drain, args=(proc.stdout, "stdout"), daemon=True)
        t_err = threading.Thread(target=_drain, args=(proc.stderr, "stderr"), daemon=True)
        t_out.start()
        t_err.start()

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            t_out.join(timeout=5)
            t_err.join(timeout=5)
            _log.error("garak timed out after %d seconds for config %s", timeout, config_path)
            raise subprocess.TimeoutExpired(proc.args, timeout)

        t_out.join()
        t_err.join()

        if proc.returncode not in (0, 1):
            _log.error("garak exited with unexpected code %d", proc.returncode)
            raise subprocess.CalledProcessError(proc.returncode, proc.args)
    finally:
        os.unlink(config_path)

    output_files = list(Path(output_dir).rglob("*.report.jsonl"))
    if not output_files:
        raise FileNotFoundError(
            f"No JSONL output file found in {output_dir} after garak run"
        )

    return str(max(output_files, key=lambda p: p.stat().st_mtime))
