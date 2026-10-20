"""One-shot pipeline: fetch everything, then build the site data.

    python scripts/update_all.py          # incremental (recommended)
    python scripts/update_all.py --full   # re-download all 13F tables

Order matters: 13F + politicians first (raw JSON), then build_data (site/data.js).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = [
    ("fetch_13f.py", []),
    ("fetch_politicians.py", []),
    ("build_data.py", []),
]


def main() -> int:
    full = "--full" in sys.argv
    for script, args in STEPS:
        cmd = [sys.executable, str(HERE / script), *args]
        if full and script == "fetch_13f.py":
            cmd.append("--full")
        print(f"\n$ {' '.join(Path(c).name for c in cmd)}", flush=True)
        rc = subprocess.call(cmd, cwd=HERE)
        if rc != 0:
            print(f"!! {script} exited with {rc}", flush=True)
            return rc
    print("\nDone. Open site/index.html", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
