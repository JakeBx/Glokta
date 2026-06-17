#!/usr/bin/env python3
"""Download recent advisory pages from one or more sources to disk (resumable).

Generalises ``download_cisa_advisories.py`` across the multi-source connector registry
(``report.SOURCES``: cisa / cccs / ncsc / acsc). For each source it scrapes the listing for
advisory URLs (newest-first), then downloads each detail page's HTML to
``data/advisories/<source>/<slug>.html`` and writes a per-source ``index.json`` mapping slug -> url.
Already-downloaded pages are skipped, so re-runs are cheap.

Usage:
    PYTHONPATH=src conda run -n glokta python scripts/download_advisories.py [N] [source ...]

Examples:
    python scripts/download_advisories.py 20 cccs ncsc     # 20 each from CCCS + NCSC
    python scripts/download_advisories.py 50               # 50 each from all sources

Env: ADV_DIR (default data/advisories), ADV_DELAY (seconds between fetches, 0.4).
Note: ACSC is bot-throttled and typically returns nothing here (see notebook 08).
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import httpx

from glokta.infrastructure.cti.connectors.report import SOURCES, fetch_source_index

UA = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
OUT_ROOT = os.environ.get(
    "ADV_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "advisories")
)
DELAY = float(os.environ.get("ADV_DELAY", "0.4"))


def download_source(client: httpx.Client, source: str, n: int) -> None:
    out = os.path.abspath(os.path.join(OUT_ROOT, source))
    os.makedirs(out, exist_ok=True)

    pages = max(2, (n // 10) + 1)
    try:
        urls = fetch_source_index(client, source, max_pages=pages, headers=UA)[:n]
    except Exception as exc:  # noqa: BLE001 - best-effort per source
        print(f"[{source}] index unavailable -> {type(exc).__name__} {str(exc)[:80]}")
        return
    print(f"[{source}] index: {len(urls)} advisory URLs (target {n})")

    index, downloaded, skipped, failed = {}, 0, 0, 0
    for i, url in enumerate(urls, 1):
        slug = url.rstrip("/").rsplit("/", 1)[-1]
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
            time.sleep(DELAY)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  [{source}] FAILED {slug}: {type(exc).__name__} {str(exc)[:80]}")

    with open(os.path.join(out, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1)
    print(f"[{source}] -> {out}  downloaded={downloaded} skipped={skipped} failed={failed}")


def main() -> None:
    args = sys.argv[1:]
    n = 20
    sources = []
    for a in args:
        if a.isdigit():
            n = int(a)
        elif a in SOURCES:
            sources.append(a)
        else:
            sys.exit(f"unknown source {a!r}; choose from {sorted(SOURCES)}")
    sources = sources or list(SOURCES)

    with httpx.Client(timeout=30.0, headers=UA, follow_redirects=True) as client:
        for source in sources:
            download_source(client, source, n)


if __name__ == "__main__":
    main()
