"""Turn raw filings into the analytics payload consumed by the website.

Correctness rules applied here (these matter for every number shown):

13F institutions
  * A 13F information table has one row per (manager, security). Berkshire files
    several rows for the same CUSIP (one per subsidiary), so rows are aggregated by
    (cusip, putCall) before any comparison — otherwise QoQ deltas are nonsense.
  * `value` in the post-2023 XML schema is USD; no thousand-scaling.
  * weight = value / total reported value of that filing.
  * Share-count change is the real buy/sell signal; value change also contains price
    moves. Both are reported and labelled.
  * A 13F-HR/A is usually a partial restatement, already merged at fetch time.

Politicians (STOCK Act PTR / OGE 278-T / 278e)
  * Amounts are *ranges*, never exact. Ranges are kept verbatim and the midpoint is
    never invented.
  * Only annual reports give a holdings snapshot; PTRs give a trade timeline.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime

import summary as S
from pathlib import Path

from common import CACHE, RAW, read_json, write_json


def load_names() -> tuple[dict, dict]:
    """(CUSIP -> 中文名, CUSIP -> ticker)。来源见 scripts/fetch_names.py。"""
    d = read_json(Path(CACHE) / "names.json") or {}
    return d.get("zh_by_cusip") or {}, d.get("ticker_by_cusip") or {}


def period_of(report_date: str) -> str:
    y, m, _ = report_date.split("-")
    return f"{y}Q{(int(m) - 1) // 3 + 1}"


# ---------------------------------------------------------------- institutions
def aggregate(rows: list[dict]) -> dict[tuple, dict]:
    agg: dict[tuple, dict] = {}
    for r in rows:
        key = (r["cusip"], r["putCall"] or "")
        cur = agg.get(key)
        if cur is None:
            agg[key] = {
                "cusip": r["cusip"],
                "name": r["issuer"],
                "klass": r["klass"],
                "putCall": r["putCall"] or "",
                "shares": r["shares"],
                "value": r["value"],
                "lines": 1,
            }
        else:
            cur["shares"] += r["shares"]
            cur["value"] += r["value"]
            cur["lines"] += 1
            if len(r["issuer"]) > len(cur["name"]):
                cur["name"] = r["issuer"]
    return agg


def classify(prev_shares: int | None, shares: int) -> str:
    if prev_shares is None:
        return "first"
    if prev_shares == 0:
        return "new"
    if shares == 0:
        return "exit"
    if shares > prev_shares:
        return "add"
    if shares < prev_shares:
        return "trim"
    return "hold"


ZH: dict[str, str] = {}
TK: dict[str, str] = {}


def build_institution(inst: dict) -> dict:
    quarters = []
    prev_agg: dict | None = None
    prev_total = 0

    for q in inst["quarters"]:
        agg = aggregate(q["rows"])
        total = sum(v["value"] for v in agg.values())
        holdings = []
        for key, v in agg.items():
            prev = (prev_agg or {}).get(key)
            prev_shares = prev["shares"] if prev else (0 if prev_agg else None)
            prev_value = prev["value"] if prev else 0
            weight = v["value"] / total if total else 0.0
            prev_weight = (prev_value / prev_total) if (prev and prev_total) else 0.0
            holdings.append(
                {
                    "name": v["name"],
                    "zh": ZH.get(v["cusip"], ""),
                    "ticker": TK.get(v["cusip"], ""),
                    "cusip": v["cusip"],
                    "klass": v["klass"],
                    "putCall": v["putCall"],
                    "shares": v["shares"],
                    "value": v["value"],
                    "weight": weight,
                    "prevShares": prev_shares,
                    "prevValue": prev_value if prev else None,
                    "dShares": (v["shares"] - prev_shares) if prev_shares is not None else None,
                    "dSharesPct": ((v["shares"] / prev_shares - 1) if prev_shares else None),
                    "dValue": (v["value"] - prev_value) if prev else None,
                    "dWeight": (weight - prev_weight) if prev else None,
                    "action": classify(prev_shares, v["shares"]),
                }
            )
        exits = []
        if prev_agg:
            for key, pv in prev_agg.items():
                if key in agg:
                    continue
                exits.append(
                    {
                        "name": pv["name"],
                        "zh": ZH.get(pv["cusip"], ""),
                        "ticker": TK.get(pv["cusip"], ""),
                        "cusip": pv["cusip"],
                        "klass": pv["klass"],
                        "putCall": pv["putCall"],
                        "shares": 0,
                        "value": 0,
                        "weight": 0.0,
                        "prevShares": pv["shares"],
                        "prevValue": pv["value"],
                        "dShares": -pv["shares"],
                        "dSharesPct": -1.0,
                        "dValue": -pv["value"],
                        "dWeight": -(pv["value"] / prev_total if prev_total else 0),
                        "action": "exit",
                    }
                )

        holdings.sort(key=lambda h: -h["value"])
        exits.sort(key=lambda h: -h["prevValue"])
        top10 = holdings[:10]
        counts = defaultdict(int)
        for h in holdings:
            counts[h["action"]] += 1
        counts["exit"] = len(exits)

        quarters.append(
            {
                "period": period_of(q["reportDate"]),
                "reportDate": q["reportDate"],
                "filingDate": q["filingDate"],
                "accession": q["accession"],
                "form": q["form"],
                "docs": q["docs"],
                "amendments": q.get("amendments", []),
                "tableUrl": q["tableUrl"],
                "primaryDocUrl": q["primaryDocUrl"],
                "filingIndexUrl": q["filingIndexUrl"],
                "total": total,
                "positions": len(holdings),
                "holdings": holdings,
                "top10": top10,
                "exits": exits,
                "counts": dict(counts),
                "concentration": {
                    "top1": holdings[0]["weight"] if holdings else 0,
                    "top5": sum(h["weight"] for h in holdings[:5]),
                    "top10": sum(h["weight"] for h in top10),
                    "hhi": sum(h["weight"] ** 2 for h in holdings),
                },
                "movers": {
                    "adds": sorted(
                        [h for h in holdings if h["action"] == "add" and h["dSharesPct"] is not None],
                        key=lambda h: -h["dSharesPct"],
                    )[:6],
                    "trims": sorted(
                        [h for h in holdings if h["action"] == "trim" and h["dSharesPct"] is not None],
                        key=lambda h: h["dSharesPct"],
                    )[:6],
                    "new": sorted(
                        [h for h in holdings if h["action"] == "new"], key=lambda h: -h["value"]
                    )[:6],
                    "exits": exits[:6],
                },
            }
        )
        prev_agg, prev_total = agg, total

    return {
        "id": inst["id"],
        "kind": "institution",
        "person": inst["person"],
        "filer": inst["filer"],
        "role": inst["role"],
        "note": inst["note"],
        "color": inst["color"],
        "cik": inst["cik"],
        "edgarUrl": inst["edgarUrl"],
        "quarters": quarters,
    }


# ----------------------------------------------------------------- politicians
def _parse_filed(filed: str | None) -> tuple[int | None, str]:
    """Return (year, ISO date) for a filing date, which may be ISO or m/d/yyyy."""
    if not filed:
        return None, ""
    filed = filed.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", filed):
        return int(filed[:4]), filed
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", filed)
    if m:
        mo, dy, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if yr < 100:
            yr += 2000
        return yr, f"{yr:04d}-{mo:02d}-{dy:02d}"
    m = re.match(r"(\d{4})", filed)
    return (int(m.group(1)), "") if m else (None, "")


def iso_date(d: str, filed: str | None = None) -> str:
    """Normalize a disclosed transaction date, repairing OCR year errors.

    These PDFs are OCR'd, so years get misread (2026 -> 2028, 7026, or truncated).
    A transaction is always disclosed after it happened (STOCK Act: <= 45 days), so
    any year later than the filing year is corrected, and a date after the filing
    date is rolled back a year.
    """
    parts = re.split(r"[/\-]", (d or "").strip())
    if len(parts) != 3:
        return ""
    m, dd, y = (re.sub(r"\D", "", x) for x in parts)
    if not m or not dd:
        return ""
    month, day = int(m), int(dd)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""

    filed_year, filed_iso = _parse_filed(filed)
    year = int(y) if y else None
    if year is not None and len(y) == 2:
        year += 2000

    if filed_year:
        # OCR'd years are off by a small amount or scrambled; the filing date is
        # the hard anchor.
        if year is None or year > filed_year or year < filed_year - 2:
            year = filed_year
    elif year is None or not (1990 <= year <= 2100):
        return ""

    iso = f"{year:04d}-{month:02d}-{day:02d}"
    if filed_iso and iso > filed_iso:
        iso = f"{year - 1:04d}-{month:02d}-{day:02d}"
    return iso


def issuer_key(desc: str) -> str:
    """Municipal-bond descriptions carry coupon/maturity text; keep the issuer only."""
    d = re.sub(r"\s+", " ", (desc or "").strip())
    d = re.split(r"\s+(?:\d+(?:\.\d+)?\s*%|DUE|REG\s+S|B/E|BIE)", d, maxsplit=1, flags=re.I)[0]
    return d[:48].strip() or desc[:48]


def build_politician(p: dict) -> dict:
    trades, filings_meta = [], []
    for f in p["filings"]["ptr"]:
        tx = f.get("transactions", [])
        filings_meta.append(
            {
                "docId": f["docId"],
                "type": f["type"],
                "date": f["filedOn"],
                "url": f["url"],
                "txCount": len(tx),
                "label": f.get("label") or f["type"],
            }
        )
        for t in tx:
            rec = dict(t)
            # House PTR uses `asset`; OGE 278-T uses `description`
            rec["asset"] = t.get("asset") or t.get("description") or ""
            rec.setdefault("ticker", None)
            rec["filingUrl"] = f["url"]
            rec["filingId"] = f["docId"]
            rec["filedOn"] = f["filedOn"]
            rec["iso"] = iso_date(t["date"], f["filedOn"])
            rec["filedIso"] = iso_date(f["filedOn"])
            trades.append(rec)

    for f in p["filings"]["annual"]:
        filings_meta.append(
            {
                "docId": f["docId"],
                "type": "ANNUAL",
                "date": f["filedOn"],
                "url": f["url"],
                "txCount": len(f.get("holdings", [])),
                "label": f.get("label") or "Annual Report",
            }
        )

    # A 278-T can be filed more than once (original + amended + duplicate uploads).
    # Keep one row per (date, kind, description, amount range).
    seen, dedup = set(), []
    for t in sorted(trades, key=lambda x: (x["iso"], x["filedIso"], x["filingId"])):
        key = (t["iso"], t["kind"], t["asset"][:70], t.get("amountLow"), t.get("amountHigh"), t.get("row"))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(t)
    trades = dedup
    trades.sort(key=lambda t: (t["iso"], t["filingId"]))

    per: dict[str, dict] = {}
    for t in trades:
        key = t.get("ticker") or issuer_key(t["asset"])
        cur = per.setdefault(
            key,
            {
                "label": t.get("ticker") or t["asset"],
                "asset": t["asset"],
                "ticker": t.get("ticker"),
                "assetType": t.get("assetType"),
                "buys": 0,
                "sells": 0,
                "buyLow": 0,
                "buyHigh": 0,
                "sellLow": 0,
                "sellHigh": 0,
                "lastDate": t["iso"],
            },
        )
        if t["kind"] == "buy":
            cur["buys"] += 1
            cur["buyLow"] += t.get("amountLow") or 0
            cur["buyHigh"] += t.get("amountHigh") or 0
        elif t["kind"] == "sell":
            cur["sells"] += 1
            cur["sellLow"] += t.get("amountLow") or 0
            cur["sellHigh"] += t.get("amountHigh") or 0
        if t["iso"] > cur["lastDate"]:
            cur["lastDate"] = t["iso"]
    tickers = sorted(per.values(), key=lambda x: -(x["buyLow"] + x["sellLow"]))[:40]

    # holdings snapshots from annual reports
    snapshots = []
    for f in p["filings"]["annual"]:
        items = []
        for h in f.get("holdings", []):
            if not h.get("valueText"):
                continue
            items.append(
                {
                    "name": h.get("name") or h.get("description") or "",
                    "ticker": h.get("ticker"),
                    "assetType": h.get("assetType"),
                    "owner": h.get("owner"),
                    "valueLow": h.get("valueLow") or 0,
                    "valueHigh": h.get("valueHigh") or 0,
                    "valueText": h["valueText"],
                    "incomeType": h.get("incomeType"),
                    "note": h.get("note"),
                    "page": h.get("page"),
                }
            )
        items.sort(key=lambda x: -x["valueLow"])
        total_low = sum(i["valueLow"] for i in items)
        total_high = sum(i["valueHigh"] for i in items)
        for i in items:
            i["weight"] = (i["valueLow"] / total_low) if total_low else 0.0
        snapshots.append(
            {
                "filedOn": f["filedOn"],
                "year": f.get("year"),
                "docId": f["docId"],
                "url": f["url"],
                "label": f.get("label") or "Annual Report",
                "items": items,
                "top10": items[:10],
                "count": len(items),
                "totalLow": total_low,
                "totalHigh": total_high,
            }
        )
    snapshots.sort(key=lambda s: s["filedOn"])

    years = sorted({t["iso"][:4] for t in trades if t["iso"]})
    return {
        "id": p["id"],
        "kind": "politician",
        "person": p["person"],
        "filer": p["filer"],
        "role": p["role"],
        "note": p["note"],
        "color": p["color"],
        "filings": sorted(filings_meta, key=lambda f: iso_date(f["date"]) or f["date"]),
        "trades": trades,
        "tickers": tickers,
        "snapshots": snapshots,
        "years": years,
    }


# ---------------------------------------------------------------------- cross
def build_cross(institutions: list[dict]) -> list[dict]:
    """Same-quarter overlap table across the three institutions (by CUSIP)."""
    periods = sorted({q["period"] for i in institutions for q in i["quarters"]})
    out = []
    for period in periods:
        by_cusip: dict[str, dict] = {}
        present = []
        for inst in institutions:
            q = next((x for x in inst["quarters"] if x["period"] == period), None)
            if not q:
                continue
            present.append({"id": inst["id"], "person": inst["person"], "color": inst["color"],
                            "total": q["total"], "positions": q["positions"]})
            for h in q["holdings"]:
                cur = by_cusip.setdefault(
                    h["cusip"],
                    {
                        "cusip": h["cusip"],
                        "name": h["name"],
                        "zh": h.get("zh") or "",
                        "ticker": h.get("ticker") or "",
                        "byInst": {},
                    },
                )
                cur["byInst"][inst["id"]] = {
                    "name": h["name"],
                    "shares": h["shares"],
                    "value": h["value"],
                    "weight": h["weight"],
                    "action": h["action"],
                    "dSharesPct": h["dSharesPct"],
                }
        rows = []
        for v in by_cusip.values():
            if len(v["byInst"]) < 2:
                continue
            rows.append({**v, "holders": len(v["byInst"]),
                         "maxValue": max(x["value"] for x in v["byInst"].values())})
        rows.sort(key=lambda r: (-r["holders"], -r["maxValue"]))
        if rows:
            out.append({"period": period, "present": present, "rows": rows})
    return out


def main() -> int:
    global ZH, TK
    ZH, TK = load_names()
    raw13f = read_json(RAW / "13f_raw.json")
    rawpol = read_json(RAW / "politicians_raw.json") or {"people": []}

    institutions = [build_institution(i) for i in raw13f["institutions"]]
    politicians = [build_politician(p) for p in rawpol["people"]]
    cross = build_cross(institutions)

    # 本期变化摘要：每人/机构一组（最新一期在前），数字全部来自上面已算好的字段
    for inst in institutions:
        inst["summaries"] = S.institution_summaries(inst)
    for p in politicians:
        p["summaries"] = [S.politician_summary(p)]
    briefs = []
    for inst in institutions:
        sm = inst["summaries"][-1]   # ascending by quarter: last = latest
        briefs.append(
            {
                "id": inst["id"],
                "kind": "institution",
                "person": inst["person"],
                "filer": inst["filer"],
                "role": inst["role"],
                "color": inst["color"],
                "period": sm["period"],
                "prevPeriod": sm["prevPeriod"],
                "headline": S.headline_for("institution", inst, sm),
                "summary": sm,
            }
        )
    for p in politicians:
        sm = p["summaries"][0]       # already the latest filing
        briefs.append(
            {
                "id": p["id"],
                "kind": "politician",
                "person": p["person"],
                "filer": p["filer"],
                "role": p["role"],
                "color": p["color"],
                "period": sm["period"],
                "prevPeriod": sm["prevPeriod"],
                "headline": S.headline_for("politician", p, sm),
                "summary": sm,
            }
        )

    latest_period = max(q["reportDate"] for i in institutions for q in i["quarters"])
    latest_filing = max(q["filingDate"] for i in institutions for q in i["quarters"])
    last_trade = max((t["iso"] for p in politicians for t in p["trades"] if t["iso"]), default="")

    # builtAt must NOT change when nothing else did, otherwise every CI run creates
    # a commit. It is therefore derived from a hash of the payload instead of now().
    fingerprint = hashlib.sha256(
        json.dumps(
            {"i": institutions, "p": politicians, "c": cross},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    # Derive the display timestamp from the data itself so it is identical on every
    # machine: latest 13F filing date + latest disclosed trade date.
    built_at = f"{latest_filing} / {last_trade or '—'}"

    data = {
        "meta": {
            "generatedAt": raw13f.get("generatedAt"),
            "builtAt": built_at,
            "fingerprint": fingerprint,
            "latestPeriod": latest_period,
            "latestFilingDate": latest_filing,
            "lastTradeDate": last_trade,
            "sources": {
                "sec": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=13F",
                "house": "https://disclosures-clerk.house.gov/FinancialDisclosure",
                "oge": "https://www.oge.gov/web/OGE.nsf/Officials%20Individual%20Disclosures%20Search%20Collection",
            },
        },
        "institutions": institutions,
        "politicians": politicians,
        "cross": cross,
        "briefs": briefs,
    }

    # ship as a JS file so the page also works when opened from disk (file://)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    (RAW.parent.parent / "site" / "data.js").write_text(
        "window.SITE_DATA=" + payload + ";\n", encoding="utf-8", newline=chr(10)
    )
    write_json(RAW.parent / "site_data.json", data)
    print(
        f"site/data.js: {len(institutions)} institutions, {len(politicians)} politicians, "
        f"{len(cross)} cross-holdings periods, latest 13F {latest_period}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
