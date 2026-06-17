#!/usr/bin/env python3
"""Download the latest N CISA AA-series advisory pages to disk (resumable).

Pages the Cybersecurity-Advisories listing for AA-series URLs (newest-first), then downloads
each detail page's HTML to ``data/cisa_advisories/<slug>.html`` and writes an ``index.json``
mapping slug -> url. Already-downloaded pages are skipped, so re-runs are cheap.

Usage:
    PYTHONPATH=src conda run -n glokta python scripts/download_cisa_advisories.py [N]

Env: CISA_ADV_DIR (default data/cisa_advisories), CISA_ADV_DELAY (seconds between fetches, 0.4).
"""

import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import httpx

from glokta.infrastructure.cti.connectors.report import fetch_aa_advisory_index

UA = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
OUT_DIR = os.environ.get(
    "CISA_ADV_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "cisa_advisories")
)
DELAY = float(os.environ.get("CISA_ADV_DELAY", "0.4"))


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    out = os.path.abspath(OUT_DIR)
    os.makedirs(out, exist_ok=True)

    with httpx.Client(timeout=30.0, headers=UA, follow_redirects=True) as client:
        pages = math.ceil(n / 10) + 1  # 10 advisories per listing page
        urls = fetch_aa_advisory_index(client, max_pages=pages, headers=UA)[:n]
        print(f"index: {len(urls)} AA advisory URLs (target {n})")

        index = {}
        downloaded = skipped = failed = 0
        for i, url in enumerate(urls, 1):
            slug = url.rsplit("/", 1)[-1]
            index[slug] = url
            path = os.path.join(out, slug + ".html")
            if os.path.exists(path) and os.path.getsize(path) > 0:
                skipped += 1
                continue
            try:
                resp = client.get(url)
                resp.raise_for_status()
                with open(path, "w", encoding="utf-8") as f:
                    f.write(resp.text)
                downloaded += 1
                if i % 10 == 0 or i == len(urls):
                    print(f"  [{i}/{len(urls)}] downloaded={downloaded} skipped={skipped} failed={failed}")
                time.sleep(DELAY)
            except Exception as exc:
                failed += 1
                print(f"  FAILED {slug}: {type(exc).__name__} {str(exc)[:80]}")

        with open(os.path.join(out, "index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, indent=1)

    print(f"\ndone -> {out}")
    print(f"downloaded={downloaded} skipped={skipped} failed={failed} total={len(urls)}")


if __name__ == "__main__":
    main()
