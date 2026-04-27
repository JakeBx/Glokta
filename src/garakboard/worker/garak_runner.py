"""Utilities for spawning and managing garak subprocess runs."""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Default timeout — overridden by settings.garak_timeout_seconds at call sites.
GARAK_TIMEOUT_SECONDS = 3600


def build_garak_config(
    model_name: str,
    probe_categories: list[str],
    output_dir: str,
    parallel_attempts: int = 1,
    rpm_limit: int | None = None,
    soft_probe_prompt_cap: int | None = None,
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
    # If probe_categories is empty, use a default subset safe for testing
    probes = probe_categories if probe_categories else ["encoding"]

    config: dict = {
        "system": {
            "parallel_attempts": parallel_attempts,
        },
        "plugins": {
            "target_type": "litellm",
            "target_name": model_name,
            "probe_spec": ",".join(probes),
        },
        "reporting": {
            "report_dir": output_dir,
        },
    }

    if rpm_limit is not None:
        # Pass rate limit through to garak so it respects the OpenRouter plan tier
        config["system"]["generators_options"] = {
            "max_requests_per_minute": rpm_limit,
        }

    if soft_probe_prompt_cap is not None:
        config["plugins"]["soft_probe_prompt_cap"] = soft_probe_prompt_cap

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
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # Always log garak output for diagnostics
        if result.stdout:
            logger.info("garak stdout:\n%s", result.stdout[-2000:])
        if result.stderr:
            logger.info("garak stderr:\n%s", result.stderr[-2000:])
    except subprocess.CalledProcessError as exc:
        logger.error(
            "garak exited with code %d.\nstdout: %s\nstderr: %s",
            exc.returncode,
            exc.stdout,
            exc.stderr,
        )
        raise
    except subprocess.TimeoutExpired as exc:
        logger.error(
            "garak timed out after %d seconds for config %s",
            timeout,
            config_path,
        )
        raise
    finally:
        os.unlink(config_path)

    # Find the JSONL output file garak wrote (use rglob for recursive search)
    output_files = list(Path(output_dir).rglob("*.jsonl"))
    if not output_files:
        raise FileNotFoundError(
            f"No JSONL output file found in {output_dir} after garak run"
        )

    # Return the most recently modified file
    return str(max(output_files, key=lambda p: p.stat().st_mtime))
