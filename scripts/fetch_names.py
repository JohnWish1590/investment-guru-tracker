"""Resolve CUSIP -> ticker -> 中文名, with a name-match guard.

Why a guard: OpenFIGI occasionally returns a *different* issuer for a CUSIP
(e.g. 30063P105 Exact Sciences came back as EXK Endeavour Silver). Every
candidate is therefore checked against the issuer name printed in the 13F table;
if the names do not overlap, the mapping is rejected and the site keeps the
filed English name.

Pipeline
  1. OpenFIGI   CUSIP -> candidates (ticker + issuer name), 25 req/min, 10/batch
  2. 腾讯行情    ticker -> 中文简称, ~40/batch
  3. guard      accept only when token overlap >= 0.5 with the filed name
  4. overrides  data/zh_overrides.json wins for hand-checked names

Output: data/cache/names.json
"""
from __future__ import annotations

import argparse
import re
import time

import requests

from common import BROWSER_UA, CACHE, read_json, session, write_json

NAMES = CACHE / "names.json"
OVERRIDES = CACHE.parent / "zh_overrides.json"
FIGI_URL = "https://api.openfigi.com/v3/mapping"
QT_URL = "https://qt.gtimg.cn/q="
FIGI_BATCH = 10
FIGI_SLEEP = 2.6
QT_BATCH = 40

CJK = re.compile("[\u4e00-\u9fff]")

STOP = {"inc", "corp", "corporation", "co", "ltd", "limited", "plc", "the", "nv", "sa",
        "ag", "holdings", "holding", "group", "company", "com", "new", "del", "class",
        "cl", "series", "trust", "fund", "etf", "shs", "ordinary", "ord", "sponsored",
        "adr", "ads", "american", "depositary", "receipts", "registered", "reg",
        "switz", "cayman", "islands", "ltd/cayman"}


def tokens(name: str) -> set[str]:
    """Lowercase word tokens, apostrophes removed, generic corporate words dropped."""
    t = re.sub(r"['’]", "", (name or "").lower())
    t = re.sub(r"[^a-z0-9 ]", " ", t).split()
    return {w for w in t if len(w) >= 2 and w not in STOP}


def _tok_match(a: str, b: str) -> bool:
    """Treat moody/moodys or teknology/technology as the same token."""
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    return False


def similarity(filed: str, candidate: str) -> float:
    """Share of the shorter token set that also appears in the longer one."""
    a, b = tokens(filed), tokens(candidate)
    if not a or not b:
        return 0.0
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    hit = sum(1 for w in small if any(_tok_match(w, x) for x in big))
    return hit / len(small)


def normalize_ticker(t: str | None) -> str | None:
    """13F/OpenFIGI use BRK/B, quote APIs want BRK.B."""
    if not t:
        return None
    return t.replace("/", ".").strip()


def load() -> dict:
    d = read_json(NAMES) or {}
    d.setdefault("cusips", {})
    d.setdefault("zh", {})
    return d


def filed_names() -> dict[str, str]:
    """cusip -> issuer name as printed in the 13F table (longest variant wins)."""
    data = read_json(CACHE.parent / "site_data.json") or {}
    out: dict[str, str] = {}
    for inst in data.get("institutions", []):
        for q in inst["quarters"]:
            for h in q["holdings"]:
                n = h["name"]
                if len(n) > len(out.get(h["cusip"], "")):
                    out[h["cusip"]] = n
    return out


def openfigi(s, cusips: list[str], store: dict, filed: dict[str, str]) -> tuple[int, int]:
    ok = rejected = 0
    todo = [c for c in cusips if c not in store]
    for i in range(0, len(todo), FIGI_BATCH):
        batch = todo[i : i + FIGI_BATCH]
        payload = [{"idType": "ID_CUSIP", "idValue": c} for c in batch]
        r = None
        for attempt in range(4):
            try:
                r = s.post(FIGI_URL, json=payload, timeout=45)
                if r.status_code == 200:
                    break
                if r.status_code == 429:
                    time.sleep(20 * (attempt + 1))
                    continue
            except requests.RequestException:
                time.sleep(5 * (attempt + 1))
        if r is None or r.status_code != 200:
            print(f"  openfigi batch {i}: {getattr(r, 'status_code', 'error')}", flush=True)
            continue
        for cusip, item in zip(batch, r.json()):
            data = item.get("data") or []
            want = filed.get(cusip, "")
            # only primary US listings: foreign lines return tickers like "EXK GR"
            data = [c for c in data if c.get("exchCode") == "US"]
            best, best_score = None, 0.0
            for cand in data:
                sc = similarity(want, cand.get("name") or "")
                # prefer the primary US listing when names tie
                if cand.get("exchCode") == "US":
                    sc += 0.01
                if sc > best_score:
                    best, best_score = cand, sc
            if best is None or best_score < 0.5:
                store[cusip] = {"ticker": None, "en": None, "rejected": bool(data)}
                rejected += 1
            else:
                store[cusip] = {
                    "ticker": normalize_ticker(best.get("ticker")),
                    "en": best.get("name"),
                }
                ok += 1
        time.sleep(FIGI_SLEEP)
    return ok, rejected


def tencent_zh(s, tickers: list[str], store: dict, filed: dict[str, str], by_ticker: dict[str, str]) -> int:
    added = 0
    todo = [t for t in tickers if t and t not in store]
    for i in range(0, len(todo), QT_BATCH):
        batch = todo[i : i + QT_BATCH]
        try:
            r = s.get(QT_URL + ",".join("us" + t for t in batch), timeout=30)
            r.encoding = "gbk"
        except requests.RequestException:
            continue
        for line in r.text.strip().split("\n"):
            if '="' not in line:
                continue
            parts = line.split('="', 1)[1].rstrip('";').split("~")
            if len(parts) < 3:
                continue
            symbol = parts[2].split(".")[0]
            zh, en = parts[1], (parts[46] if len(parts) > 46 else "")
            if not symbol or not zh:
                continue
            if not CJK.search(zh):
                # the quote API returns an English name for many foreign issuers;
                # an English string is not a Chinese name, so do not store it
                continue
            # second guard: the quote API's English name must match the filing too
            want = filed.get(by_ticker.get(symbol, ""), "")
            if want and en and similarity(want, en) < 0.34:
                continue
            store[symbol] = zh
            added += 1
        time.sleep(0.35)
    return added


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-zh", action="store_true")
    ap.add_argument("--refresh-figi", action="store_true")
    args = ap.parse_args()

    s = session(BROWSER_UA)
    store = load()
    if args.refresh_figi:
        store["cusips"] = {}
    if args.refresh_zh:
        store["zh"] = {}

    filed = filed_names()
    ok, rej = openfigi(s, list(filed), store["cusips"], filed)
    tickers = sorted({v["ticker"] for v in store["cusips"].values() if v.get("ticker")})
    by_ticker = {v["ticker"]: c for c, v in store["cusips"].items() if v.get("ticker")}
    n2 = tencent_zh(s, tickers, store["zh"], filed, by_ticker)

    # hand-checked overrides win (keyed by CUSIP, or by ticker when CUSIP unknown)
    ov = read_json(OVERRIDES) or {}
    by_cusip: dict[str, str] = {}
    for cusip, v in store["cusips"].items():
        t = v.get("ticker")
        if t and t in store["zh"]:
            by_cusip[cusip] = store["zh"][t]
    for key, val in ov.items():
        if not isinstance(val, str):
            continue
        if key.isupper() and key in store["zh"]:
            store["zh"][key] = val
        else:
            by_cusip[key] = val
    store["zh_by_cusip"] = by_cusip
    store["ticker_by_cusip"] = {c: v["ticker"] for c, v in store["cusips"].items() if v.get("ticker")}

    store["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_json(NAMES, store)
    mapped = sum(1 for v in store["cusips"].values() if v.get("ticker"))
    print(f"cusips={len(filed)} tickers={mapped} zh={len(store['zh'])} usable={len(store['zh_by_cusip'])}")
    print(f"new: +{ok} figi, +{n2} zh, rejected {rej}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
