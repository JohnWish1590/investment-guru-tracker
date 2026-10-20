"""Shared helpers: HTTP session, caching, JSON IO.

All data comes from primary government sources:
  * SEC EDGAR (13F-HR information tables)  -> https://www.sec.gov
  * U.S. House Clerk (STOCK Act PTR PDFs)  -> https://disclosures-clerk.house.gov
  * U.S. OGE (OGE Form 278-T PDFs)         -> https://extapps2.oge.gov
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
CACHE = DATA / "cache"
LOGS = ROOT / "logs"
SITE = ROOT / "site"

for _p in (DATA, RAW, CACHE, LOGS, SITE):
    _p.mkdir(parents=True, exist_ok=True)

SEC_UA = "CindyResearch/1.0 (investment-guru-tracker; contact: research@example.com)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def session(ua: str = SEC_UA) -> requests.Session:
    s = requests.Session()
    # A local HTTP proxy breaks TLS to sec.gov / house.gov; go direct.
    s.trust_env = False
    s.headers.update({"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
    return s


def get(s: requests.Session, url: str, *, tries: int = 4, timeout: int = 60, **kw):
    """GET with light exponential backoff. SEC asks for <=10 req/s."""
    last = None
    for i in range(tries):
        try:
            r = s.get(url, timeout=timeout, **kw)
            if r.status_code == 200:
                time.sleep(0.15)  # stay polite to SEC
                return r
            last = RuntimeError(f"HTTP {r.status_code} for {url}")
            if r.status_code in (403, 404):
                raise last
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(1.5 * (i + 1))
    raise last  # type: ignore[misc]


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with (LOGS / "daily.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
