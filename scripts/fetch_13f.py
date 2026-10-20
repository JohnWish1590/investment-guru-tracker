"""Fetch SEC 13F-HR filings (information tables) for the tracked institutions.

Every position carries: accession number, filing date, report date, and the exact
EDGAR document URL, so the web page can cite the original disclosure for every
single number.

Institutions
------------
  Duan Yongping  -> H&H International Investment, LLC (CIK 1759760)
  Warren Buffett-> Berkshire Hathaway Inc (CIK 1067983)
  Cathie Wood   -> ARK Investment Management LLC (CIK 1697748)

Usage:
    python fetch_13f.py            # incremental (only new accessions)
    python fetch_13f.py --full     # re-download every filing kept
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from common import CACHE, RAW, SEC_UA, get, log, read_json, session, write_json

INSTITUTIONS = [
    {
        "id": "duan",
        "cik": "0001759760",
        "cik_int": 1759760,
        "filer": "H&H International Investment, LLC",
        "person": "段永平",
        "role": "个人/家族投资公司（H&H International Investment）",
        "note": "段永平在美国 SEC 备案的投资主体；13F 由其向 SEC 申报。",
        "color": "#2f7d6e",
    },
    {
        "id": "buffett",
        "cik": "0001067983",
        "cik_int": 1067983,
        "filer": "Berkshire Hathaway Inc",
        "person": "沃伦·巴菲特",
        "role": "伯克希尔·哈撒韦（Berkshire Hathaway）",
        "note": "13F 由伯克希尔·哈撒韦整体申报，含保险与铁路以外全部证券持仓。",
        "color": "#1f5f9e",
    },
    {
        "id": "wood",
        "cik": "0001697748",
        "cik_int": 1697748,
        "filer": "ARK Investment Management LLC",
        "person": "凯瑟琳·伍德（木头姐）",
        "role": "ARK Invest（ARK Investment Management）",
        "note": "ARK 以主动 ETF 为主，13F 覆盖 ARKK / ARKQ / ARKW / ARKG 等基金合计持仓。",
        "color": "#a3437a",
    },
]

KEEP_QUARTERS = 9  # keep 8 latest quarters + prior-year same quarter for YoY


def submissions(s, inst) -> dict:
    path = CACHE / f"submissions_{inst['cik']}.json"
    d = read_json(path)
    if d is None:
        url = f"https://data.sec.gov/submissions/CIK{inst['cik']}.json"
        d = get(s, url).json()
        write_json(path, d)
    return d


def filings_13f(sub: dict) -> list[dict]:
    r = sub["filings"]["recent"]
    out = []
    for i, form in enumerate(r["form"]):
        if not form.startswith("13F-HR"):
            continue
        out.append(
            {
                "accession": r["accessionNumber"][i],
                "form": form,
                "filingDate": r["filingDate"][i],
                "reportDate": r["reportDate"][i],
                "primaryDoc": r["primaryDocument"][i],
                "acceptance": r.get("acceptanceDateTime", [None] * len(r["form"]))[i],
            }
        )
    out.sort(key=lambda f: (f["reportDate"], f["filingDate"]))
    return out


def pick_quarters(all_filings: list[dict], keep: int) -> list[dict]:
    """Group filings by report period and pick the base filing + amendments.

    A 13F-HR/A is usually a *partial* restatement (only the corrected rows), so the
    consolidated table is: base 13F-HR  +  every 13F-HR/A applied in filing order.
    """
    by_period: dict[str, list[dict]] = {}
    for f in all_filings:
        by_period.setdefault(f["reportDate"], []).append(f)

    picked = []
    for period in sorted(by_period)[-keep:]:
        group = sorted(by_period[period], key=lambda f: (f["filingDate"], f["accession"]))
        bases = [f for f in group if f["form"] == "13F-HR"]
        base = bases[-1] if bases else group[0]
        rest = [f for f in group if f is not base]
        base = dict(base)
        base["amendments"] = [
            {"accession": a["accession"], "form": a["form"], "filingDate": a["filingDate"]}
            for a in rest
        ]
        base["extraFilings"] = rest
        picked.append(base)
    return picked


NUM = re.compile(r"-?\d+")


def parse_table(xml_text: str) -> list[dict]:
    """Parse a 13F information table, namespace-agnostic."""
    xml_text = xml_text.lstrip("\ufeff")
    root = ET.fromstring(xml_text)
    rows = []
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag != "infoTable":
            continue
        rec = {}
        for child in node:
            ctag = child.tag.split("}")[-1]
            if ctag == "shrsOrPrnAmt":
                for g in child:
                    rec["shares" if g.tag.split("}")[-1] == "sshPrnamt" else "shareType"] = (
                        (g.text or "").strip()
                    )
            elif ctag == "votingAuthority":
                continue
            elif ctag == "otherManager":
                rec["otherManager"] = (child.text or "").strip()
            else:
                rec[ctag] = (child.text or "").strip()
        if not rec.get("cusip"):
            continue
        try:
            value = int(float(rec.get("value", "0") or 0))
        except ValueError:
            value = 0
        try:
            shares = int(float(rec.get("shares", "0") or 0))
        except ValueError:
            shares = 0
        rows.append(
            {
                "issuer": (rec.get("nameOfIssuer") or "").strip(),
                "klass": (rec.get("titleOfClass") or "").strip(),
                "cusip": rec["cusip"].strip().upper(),
                "value": value,
                "shares": shares,
                "shareType": rec.get("shareType") or "SH",
                "putCall": (rec.get("putCall") or "").strip(),
                "discretion": (rec.get("investmentDiscretion") or "").strip(),
            }
        )
    return rows


def find_table_doc(s, inst, filing) -> tuple[str, str]:
    """Return (table_xml_url, primary_doc_url)."""
    base = (
        f"https://www.sec.gov/Archives/edgar/data/{inst['cik_int']}/"
        f"{filing['accession'].replace('-', '')}/"
    )
    idx = get(s, base + "index.json").json()
    names = [i["name"] for i in idx["directory"]["item"]]
    xmls = [n for n in names if n.lower().endswith(".xml") and "primary_doc" not in n.lower()]
    if not xmls:
        xmls = [n for n in names if n.lower().endswith(".xml")]
    table = xmls[0] if xmls else None
    return base + (table or ""), base + "primary_doc.xml", base


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="re-download all kept filings")
    args = ap.parse_args()

    s = session(SEC_UA)
    out = {
        "generatedAt": None,   # filled with the latest filing date below
        "source": "SEC EDGAR 13F-HR information tables",
        "institutions": [],
    }
    # generatedAt must be a property of the DATA, not of when the script ran:
    # otherwise every CI run produces a diff and a spurious commit.
    out["generatedAt"] = None

    for inst in INSTITUTIONS:
        sub = submissions(s, inst)
        allf = filings_13f(sub)
        picked = pick_quarters(allf, KEEP_QUARTERS)
        log(f"{inst['person']}: {len(allf)} 13F filings, keeping {len(picked)} periods")

        quarters = []
        for f in picked:
            acc = f["accession"]
            cache_path = CACHE / f"table_{inst['id']}_{acc}.json"
            cached = None if args.full else read_json(cache_path)
            if cached:
                quarters.append(cached)
                continue

            docs = []
            rows: list[dict] = []
            for part in [f] + f.get("extraFilings", []):
                table_url, primary_url, base = find_table_doc(s, inst, part)
                if not table_url.endswith(".xml"):
                    log(f"  !! no XML table for {part['accession']}")
                    continue
                part_rows = parse_table(get(s, table_url).text)
                docs.append(
                    {
                        "accession": part["accession"],
                        "form": part["form"],
                        "filingDate": part["filingDate"],
                        "tableUrl": table_url,
                        "primaryDocUrl": primary_url,
                        "filingIndexUrl": base,
                        "rowCount": len(part_rows),
                    }
                )
                if part is f:
                    rows = part_rows
                else:
                    # amendment: overwrite matching lines, append genuinely new ones
                    key = {(r["cusip"], r["putCall"], r["shares"]): i for i, r in enumerate(rows)}
                    for r in part_rows:
                        k = (r["cusip"], r["putCall"], r["shares"])
                        if k in key:
                            rows[key[k]] = r
                        else:
                            rows.append(r)

            rec = {
                "accession": acc,
                "form": f["form"],
                "filingDate": f["filingDate"],
                "reportDate": f["reportDate"],
                "amendments": f.get("amendments", []),
                "docs": docs,
                "tableUrl": docs[0]["tableUrl"] if docs else None,
                "primaryDocUrl": docs[0]["primaryDocUrl"] if docs else None,
                "filingIndexUrl": docs[0]["filingIndexUrl"] if docs else None,
                "sourceUrl": (
                    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                    f"&CIK={inst['cik']}&type=13F&dateb=&owner=include&count=40"
                ),
                "rows": rows,
            }
            write_json(cache_path, rec)
            log(f"  + {acc} {f['reportDate']} rows={len(rows)} docs={len(docs)}")
            quarters.append(rec)

        out["institutions"].append(
            {
                "id": inst["id"],
                "person": inst["person"],
                "filer": inst["filer"],
                "role": inst["role"],
                "note": inst["note"],
                "color": inst["color"],
                "cik": inst["cik"],
                "edgarUrl": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={inst['cik']}&type=13F&dateb=&owner=include&count=40",
                "quarters": quarters,
            }
        )

    latest = max(
        (q["filingDate"] for inst in out["institutions"] for q in inst["quarters"]),
        default=None,
    )
    out["generatedAt"] = latest
    write_json(RAW / "13f_raw.json", out)
    log(f"wrote {RAW / '13f_raw.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
