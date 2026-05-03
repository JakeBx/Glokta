"""Utilities for spawning and managing garak subprocess runs."""

import functools
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Default timeout — overridden by settings.garak_timeout_seconds at call sites.
GARAK_TIMEOUT_SECONDS = 3600

DEFAULT_PROBE_CATEGORIES = [
    "encoding",        # Prompt injection — encoding evasion
    "dan",             # Jailbreaking — DAN variants
    "goodside",        # Prompt injection — Riley Goodside techniques
    "promptinject",    # Prompt injection — HouYi framework
    "malwaregen",      # Harmful content — malware generation
    "continuation",    # Harmful content — toxic text continuation
    "lmrc",            # Safety alignment — Language Model Risk Cards
    "leakreplay",      # Information leakage — training data memorization
    "snowball",        # Safety alignment — escalating false claims
    "badchars",        # Prompt injection — control character evasion
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


_GENERATOR_NAME = "openrouter-direct"
_OPENROUTER_URI = "https://openrouter.ai/api/v1/chat/completions"


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
        model_name: OpenRouter model name e.g. 'openrouter/meta-llama/llama-3-8b-instruct:free'
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
    # tasks.py always prepends "openrouter/"; REST config needs the raw model ID
    raw_model = model_name.removeprefix("openrouter/")

    config: dict = {
        "system": {
            "parallel_attempts": parallel_attempts,
        },
        "plugins": {
            "target_type": "rest",
            "target_name": _GENERATOR_NAME,
            "probe_spec": spec,
            "generators": {
                "rest": {
                    "RestGenerator": {
                        "name": _GENERATOR_NAME,
                        "uri": _OPENROUTER_URI,
                        "method": "post",
                        "headers": {
                            "Content-Type": "application/json",
                            "Authorization": "Bearer $KEY",
                        },
                        "key_env_var": "OPENROUTER_API_KEY",
                        "req_template_json_object": {
                            "model": raw_model,
                            "messages": [{"role": "user", "content": "$INPUT"}],
                            "stream": False,
                        },
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


def run_garak(config: dict, api_key: str, timeout: int = GARAK_TIMEOUT_SECONDS) -> str:
    """
    Write a garak YAML config and run garak as a subprocess.

    Args:
        config: garak config dict from build_garak_config()
        api_key: OpenRouter API key

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

    try:
        env = os.environ.copy()
        env["OPENROUTER_API_KEY"] = api_key

        result = subprocess.run(
            ["garak", "--config", config_path],
            env=env,
            check=False,  # exit 1 = vulnerabilities found — normal garak behavior
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.stdout:
            logger.info("garak stdout:\n%s", result.stdout[-2000:])
        if result.stderr:
            logger.info("garak stderr:\n%s", result.stderr[-2000:])
        if result.returncode not in (0, 1):
            logger.error(
                "garak exited with unexpected code %d.\nstdout: %s\nstderr: %s",
                result.returncode,
                result.stdout,
                result.stderr,
            )
            raise subprocess.CalledProcessError(
                result.returncode, result.args, result.stdout, result.stderr
            )
    except subprocess.TimeoutExpired as exc:
        logger.error(
            "garak timed out after %d seconds for config %s",
            timeout,
            config_path,
        )
        raise
    finally:
        os.unlink(config_path)

    output_files = list(Path(output_dir).rglob("*.report.jsonl"))
    if not output_files:
        raise FileNotFoundError(
            f"No JSONL output file found in {output_dir} after garak run"
        )

    return str(max(output_files, key=lambda p: p.stat().st_mtime))
