"""Download every OGE filing for the tracked executive-branch filer.

The OGE document server is slow (60-250s per multi-MB PDF), so downloads run in a
small thread pool and are skipped when the file already exists. Safe to re-run.
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import sys

from common import BROWSER_UA, RAW, get, log, session
from fetch_politicians import OGE_API, oge_rows

def target_name(url: str) -> str:
    name = url.split("/")[-1]
    return "trump_" + re.sub(r"[^A-Za-z0-9._-]", "_", name)


def main() -> int:
    s = session(BROWSER_UA)
    rows = oge_rows(s, "Trump, Donald")
    jobs = []
    for row in rows:
        m = re.search(r"href='([^']+)'", row["type"])
        if not m:
            continue
        url = m.group(1)
        path = RAW / target_name(url)
        if not path.exists():
            jobs.append((url, path))
    log(f"OGE downloads pending: {len(jobs)}")
    if not jobs:
        return 0

    def one(job):
        url, path = job
        try:
            path.write_bytes(get(s, url, timeout=300, tries=3).content)
            return f"ok {path.name} {path.stat().st_size}"
        except Exception as exc:  # noqa: BLE001
            return f"FAIL {path.name} {exc}"

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for msg in ex.map(one, jobs):
            log(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
