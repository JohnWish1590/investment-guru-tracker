"""Fetch + parse congressional / presidential disclosure filings.

Two very different disclosure regimes are handled here:

1. Congress (STOCK Act) — U.S. House Clerk publishes:
   * PTR  (form type "P"): periodic transaction reports, trade-by-trade
   * Annual (form type "O"): yearly holdings snapshot with value brackets
   Index: https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip
   PDFs : .../public_disc/ptr-pdfs/{year}/{DocID}.pdf
          .../public_disc/financial-pdfs/{year}/{DocID}.pdf

2. Executive branch — U.S. OGE publishes OGE Form 278-T (periodic transactions) and
   278e (annual holdings) for the President.
   API: https://extapps2.oge.gov/201/Presiden.nsf/API.xsp/v3/rest

Both regimes disclose *ranges*, never exact amounts, and only Congress publishes a
holdings snapshot (annual report). Politicians therefore get a trade timeline, and a
top-N holdings view is built from the annual report.
"""
from __future__ import annotations

import io
import re
import zipfile

import house_annual
import house_ptr
import oge_278e
import oge_278t
import parsed_cache
from common import BROWSER_UA, CACHE, RAW, get, log, read_json, session, write_json

HOUSE_INDEX = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
HOUSE_PTR = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{docid}.pdf"
HOUSE_ANNUAL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}/{docid}.pdf"
OGE_API = "https://extapps2.oge.gov/201/Presiden.nsf/API.xsp/v3/rest"

PEOPLE = [
    {
        "id": "pelosi",
        "person": "南希·佩洛西",
        "filer": "Hon. Nancy Pelosi",
        "role": "美国众议员（加州第 11 选区，前众议院议长）",
        "chamber": "house",
        "color": "#7a5c3e",
        "note": "按 STOCK Act 披露：PTR 逐笔交易 + 年度报告（含持仓区间）。金额为区间，非精确值。",
        "years": [2023, 2024, 2025, 2026],
    },
    {
        "id": "trump",
        "person": "唐纳德·特朗普",
        "filer": "Donald J. Trump",
        "role": "美国总统（白宫）",
        "chamber": "executive",
        "color": "#8a4b3c",
        "note": "按 OGE Form 278-T（定期交易）与 278e（年度持仓）披露。金额为区间，非精确值。",
        "search": "Trump, Donald",
    },
]


def _date_key(d: str) -> str:
    """House Clerk gives m/d/yyyy; sort by ISO so filings order chronologically."""
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", (d or "").strip())
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return d or ""


def house_index(s, year: int) -> list[dict]:
    cache = CACHE / f"house_index_{year}.json"
    cached = read_json(cache)
    if cached:
        return cached
    raw = get(s, HOUSE_INDEX.format(year=year), timeout=120).content
    zf = zipfile.ZipFile(io.BytesIO(raw))
    txt = [n for n in zf.namelist() if n.lower().endswith(".txt")][0]
    lines = zf.read(txt).decode("utf-8", "ignore").splitlines()
    head = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) < len(head):
            continue
        rows.append(dict(zip(head, cols)))
    write_json(cache, rows)
    return rows


def fetch_pelosi(s) -> dict:
    ptrs, annuals = [], []
    for year in PEOPLE[0]["years"]:
        for r in house_index(s, year):
            if r["Last"].strip().lower() != "pelosi":
                continue
            docid = r["DocID"].strip()
            ftype = r["FilingType"].strip()
            if ftype == "P":
                url = HOUSE_PTR.format(year=year, docid=docid)
                src = {"docId": docid, "url": url, "year": year, "filedOn": r["FilingDate"].strip()}
                tx = parsed_cache.get("pelosi_ptr", docid)
                if tx is None:
                    path = RAW / f"pelosi_ptr_{docid}.pdf"
                    if not path.exists():
                        path.write_bytes(get(s, url, timeout=120).content)
                    tx = [house_ptr.normalize(x, src) for x in house_ptr.parse_pdf(path)]
                    parsed_cache.put("pelosi_ptr", docid, tx)
                ptrs.append(
                    {
                        "docId": docid,
                        "type": "PTR",
                        "year": year,
                        "filedOn": r["FilingDate"].strip(),
                        "url": url,
                        "indexUrl": "https://disclosures-clerk.house.gov/FinancialDisclosure",
                        "transactions": tx,
                    }
                )
            elif ftype == "O":
                url = HOUSE_ANNUAL.format(year=year, docid=docid)
                holdings = parsed_cache.get("pelosi_annual", docid)
                if holdings is None:
                    path = RAW / f"pelosi_annual_{docid}.pdf"
                    if not path.exists():
                        path.write_bytes(get(s, url, timeout=120).content)
                    holdings = house_annual.parse_annual(path)
                    parsed_cache.put("pelosi_annual", docid, holdings)
                annuals.append(
                    {
                        "docId": docid,
                        "type": "ANNUAL",
                        "year": year,
                        "filedOn": r["FilingDate"].strip(),
                        "url": url,
                        "holdings": holdings,
                    }
                )
    ptrs.sort(key=lambda f: _date_key(f["filedOn"]))
    annuals.sort(key=lambda f: _date_key(f["filedOn"]))
    log(
        f"Pelosi: {len(ptrs)} PTRs / {sum(len(f['transactions']) for f in ptrs)} trades, "
        f"{len(annuals)} annual reports / {sum(len(f['holdings']) for f in annuals)} holdings"
    )
    return {"ptr": ptrs, "annual": annuals}


def oge_rows(s, query: str) -> list[dict]:
    """OGE filing index, with the on-disk copy as a fallback.

    extapps2.oge.gov is frequently unreachable from cloud IPs (connect timeout),
    so a cached index is preferred over failing the whole run. The cached index
    is a real API response — it is only used when the live call cannot complete.
    """
    cache = CACHE / "oge_trump.json"
    cached = read_json(cache)
    try:
        r = s.get(
            OGE_API,
            params={"draw": 1, "start": 0, "length": 200, "search[value]": query},
            timeout=45,
        )
        rows = [x for x in r.json()["data"] if x["agency"] == "White House Office"]
        if rows:
            write_json(cache, rows)
            return rows
        log("  !! OGE index returned no rows")
    except Exception as exc:  # noqa: BLE001
        log(f"  !! OGE index unavailable ({type(exc).__name__}); using cached copy")
    if cached:
        return cached
    raise RuntimeError("OGE index unavailable and no cache present")


def oge_path(url: str):
    return RAW / ("trump_" + re.sub(r"[^A-Za-z0-9._-]", "_", url.split("/")[-1]))


def _safe_parse(fn, path, kind: str):
    """The OGE server sometimes truncates large PDFs mid-transfer; skip and log them."""
    try:
        return fn(path)
    except Exception as exc:  # noqa: BLE001
        log(f"  !! cannot parse {kind} {path.name} ({path.stat().st_size} bytes): {type(exc).__name__}")
        bad = CACHE / "bad_pdfs.txt"
        with bad.open("a", encoding="utf-8") as fh:
            fh.write(f"{path}\t{exc}\n")
        return None


def fetch_trump(s) -> dict:
    """Parse OGE filings, preferring the cached parse over re-downloading the PDF.

    The OGE document server is slow (60-270s per multi-MB scan) and its PDFs are
    not stored in git, so a cached parse is what makes the daily CI run cheap.
    """
    ptrs, annuals = [], []
    for row in oge_rows(s, "Trump, Donald"):
        m = re.search(r"href='([^']+)'", row["type"])
        if not m:
            continue
        url = m.group(1)
        path = oge_path(url)
        is_annual = "ANNUAL" in url.upper() or "Annual" in url
        kind = "trump_278e" if is_annual else "trump_278t"

        base = {
            "docId": path.name,
            "filedOn": row["docDate"][:10],
            "amendedOn": (row["amended"] or "")[:10],
            "url": url,
            "label": re.sub(r"<[^>]+>", "", row["type"]).strip(),
        }

        parsed = parsed_cache.get(kind, path.name)
        if parsed is None:
            if not path.exists():
                try:
                    path.write_bytes(get(s, url, timeout=300, tries=2).content)
                except Exception as exc:  # noqa: BLE001
                    log(f"  !! skip {path.name}: {exc}")
                    continue
            fn = (
                (lambda p: oge_278e.parse_278e_part6(p.read_bytes()))
                if is_annual
                else (lambda p: oge_278t.parse_278t(p.read_bytes()))
            )
            parsed = _safe_parse(fn, path, "278e" if is_annual else "278-T")
            if parsed is None:
                continue
            parsed_cache.put(kind, path.name, parsed)

        if is_annual:
            base["type"] = "ANNUAL"
            base["holdings"] = parsed
            annuals.append(base)
        else:
            base["type"] = "278T"
            base["transactions"] = parsed
            ptrs.append(base)

    ptrs.sort(key=lambda f: f["filedOn"])
    annuals.sort(key=lambda f: f["filedOn"])
    log(
        f"Trump: {len(ptrs)} Form 278-T ({sum(len(f['transactions']) for f in ptrs)} trades), "
        f"{len(annuals)} annual 278e ({sum(len(f['holdings']) for f in annuals)} holdings)"
    )
    return {"ptr": ptrs, "annual": annuals}


def main() -> int:
    s = session(BROWSER_UA)
    out = {"people": []}
    for p in PEOPLE:
        filings = fetch_pelosi(s) if p["chamber"] == "house" else fetch_trump(s)
        out["people"].append({**p, "filings": filings})
    write_json(RAW / "politicians_raw.json", out, sort_keys=True)
    log("wrote politicians_raw.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
