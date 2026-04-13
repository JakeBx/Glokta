"""Utilities for spawning and managing garak subprocess runs."""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Maximum seconds a single garak run is allowed to take before the worker
# treats it as a hung process and raises subprocess.TimeoutExpired.
GARAK_TIMEOUT_SECONDS = 600


def build_garak_config(
    model_name: str,
    probe_categories: list[str],
    output_dir: str,
    rpm_limit: int = 14,
    parallel_attempts: int = 1,
) -> dict:
    """
    Build a garak configuration dict suitable for writing as YAML.

    Args:
        model_name: OpenRouter model name e.g. 'openrouter/meta-llama/llama-3-8b-instruct:free'
        probe_categories: List of probe category names e.g. ['encoding', 'malwaregen']
        output_dir: Directory where garak should write its JSONL output
        rpm_limit: Requests per minute limit (default 14)
        parallel_attempts: Number of parallel attempts (default 1)

    Returns:
        dict suitable for yaml.dump()
    """
    # If probe_categories is empty, use a default subset safe for testing
    probes = probe_categories if probe_categories else ["encoding"]

    return {
        "run": {
            "parallel_attempts": parallel_attempts,
        },
        "plugins": {
            "model_type": "litellm",
            "model_name": model_name,
            "probes": probes,
            "generators": {
                "litellm": {
                    "rpm": rpm_limit,
                }
            },
        },
        "reporting": {
            "output_dir": output_dir,
        },
    }


def run_garak(config: dict, api_key: str) -> str:
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
    output_dir = config["reporting"]["output_dir"]

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as config_file:
        yaml.dump(config, config_file)
        config_path = config_file.name

    try:
        env = os.environ.copy()
        env["OPENROUTER_API_KEY"] = api_key

        subprocess.run(
            ["garak", "--config", config_path],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=GARAK_TIMEOUT_SECONDS,
        )
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
            GARAK_TIMEOUT_SECONDS,
            config_path,
        )
        raise
    finally:
        os.unlink(config_path)

    # Find the JSONL output file garak wrote
    output_files = list(Path(output_dir).glob("*.jsonl"))
    if not output_files:
        raise FileNotFoundError(
            f"No JSONL output file found in {output_dir} after garak run"
        )

    # Return the most recently modified file
    return str(max(output_files, key=lambda p: p.stat().st_mtime))
