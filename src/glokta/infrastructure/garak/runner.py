"""Utilities for spawning and managing garak subprocess runs."""

import functools
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import yaml

from glokta.infrastructure.llm.routing import build_request_body, resolve_route

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
    "dan.AntiDAN",          # DAN: Jailbreak robustness, down sampled to manage runtime
    "dan.AutoDANCached",
    "dan.ChatGPT_Developer_Mode_RANTI",
    "dan.ChatGPT_Developer_Mode_v2",
    "dan.ChatGPT_Image_Markdown",
    "dan.DAN_Jailbreak",
    "dan.DUDE",
    "dan.DanInTheWild",
    "dan.Dan_10_0",
    "dan.Dan_11_0",
    "dan.Dan_6_0",
    "dan.Dan_6_2",
    "dan.Dan_7_0",
    "dan.Dan_8_0",
    "dan.Dan_9_0",
    "dan.STAN",
    "goodside",
    "encoding.InjectBase64",
    "encoding.InjectROT13",
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
    # Split categories into bare prefixes ("dan") and fully-qualified names ("dan.AntiDAN").
    # DEFAULT_PROBE_CATEGORIES mixes both forms.
    category_prefixes: set[str] = set()
    fully_qualified: set[str] = set()
    for entry in probe_categories:
        if "." in entry:
            fully_qualified.add(entry)
        else:
            category_prefixes.add(entry)

    result = []
    for full_name in _all_garak_probe_names():
        db_name = full_name.removeprefix("probes.")
        probe_class = db_name.split(".")[-1] if "." in db_name else db_name
        category = db_name.split(".")[0]
        if probe_class.endswith("Full") or db_name in done:
            continue
        if category in category_prefixes or db_name in fully_qualified:
            result.append(db_name)
    return result


def build_garak_config(
    model_name: str,
    probe_categories: list[str],
    output_dir: str,
    parallel_attempts: int = 1,
    rpm_limit: int | None = None,
    soft_probe_prompt_cap: int | None = None,
    probe_spec_override: str | None = None,
) -> dict:
    """Build a garak configuration dict suitable for writing as YAML."""
    probes = probe_categories if probe_categories else DEFAULT_PROBE_CATEGORIES
    spec = probe_spec_override if probe_spec_override is not None else ",".join(probes)

    route = resolve_route(model_name)
    req_body = build_request_body(
        route, [{"role": "user", "content": "$INPUT"}], stream=False
    )
    if route.suppress_thinking:
        logger.info(
            "build_garak_config: thinking suppression enabled for %s", route.raw_model
        )

    config: dict = {
        "system": {
            "parallel_attempts": parallel_attempts,
        },
        "plugins": {
            "target_type": "rest",
            "target_name": route.generator_name,
            "probe_spec": spec,
            "generators": {
                "rest": {
                    "RestGenerator": {
                        "name": route.generator_name,
                        "uri": route.uri,
                        "method": "post",
                        "headers": {
                            "Content-Type": "application/json",
                            "Authorization": "Bearer $KEY",
                        },
                        "key_env_var": route.key_env_var,
                        "req_template_json_object": req_body,
                        "response_json": True,
                        "response_json_field": "$.choices[0].message.content",
                        "request_timeout": route.request_timeout,
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
    """Write a garak YAML config and run garak as a subprocess.

    Returns:
        Path to the garak JSONL output file

    Raises:
        subprocess.CalledProcessError: If garak exits with non-zero status
        subprocess.TimeoutExpired: If garak does not complete within timeout
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
