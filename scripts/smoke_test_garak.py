#!/usr/bin/env python3
"""
Smoke test: run a minimal garak scan against gemini-2.5-flash-lite via OpenRouter.

Validates end-to-end connectivity and that a simple scan completes well under 5 minutes.
Run this before deploying or after any changes to garak/OpenRouter config.

Usage:
    OPENROUTER_API_KEY=<key> PYTHONPATH=src conda run -n open-llm-sec \\
        python scripts/smoke_test_garak.py
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from garakboard.worker.garak_runner import build_garak_config, run_garak

MODEL = "openrouter/google/gemini-2.5-flash-lite"
PROBES = ["goodside.ThreatenJSON"]
RPM_LIMIT = 200
PASS_THRESHOLD_SECONDS = 60


def main() -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        # Fall back to .env if present
        try:
            from garakboard.config import settings
            api_key = settings.openrouter_api_key
        except Exception:
            pass

    if not api_key:
        print("ERROR: OPENROUTER_API_KEY is not set.")
        print("Export it or add it to your .env file before running this script.")
        sys.exit(1)

    print(f"GarakBoard smoke test")
    print(f"  model : {MODEL}")
    print(f"  probes: {PROBES}")
    print(f"  rpm   : {RPM_LIMIT}")
    print(f"  pass  : < {PASS_THRESHOLD_SECONDS}s")
    print()

    with tempfile.TemporaryDirectory() as output_dir:
        config = build_garak_config(
            model_name=MODEL,
            probe_categories=PROBES,
            output_dir=output_dir,
            parallel_attempts=1,
            rpm_limit=RPM_LIMIT,
        )
        config["run"] = {"generations": 1}

        start = time.monotonic()
        print("Running garak... (this may take a minute)")

        try:
            run_garak(config, api_key)
        except Exception as exc:
            elapsed = time.monotonic() - start
            print(f"\nFAIL — garak raised an exception after {elapsed:.1f}s")
            print(f"  {exc}")
            sys.exit(1)

        elapsed = time.monotonic() - start

    if elapsed < PASS_THRESHOLD_SECONDS:
        print(f"PASS — completed in {elapsed:.1f}s (threshold: {PASS_THRESHOLD_SECONDS}s)")
    else:
        print(f"FAIL — completed but took {elapsed:.1f}s (threshold: {PASS_THRESHOLD_SECONDS}s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
