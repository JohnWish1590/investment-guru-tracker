"""Daily change detection for all tracked disclosures.

Checks the three official sources for anything newer than what is already stored:

  1. SEC 13F-HR / 13F-HR/A  — new accession numbers per institution
  2. U.S. House Clerk        — new PTR ("P") or annual ("O") filings for the member
  3. U.S. OGE                — new 278-T / 278e filings for the President

Exit codes: 0 = no change, 10 = changes found and data rebuilt, 1 = error.
The state file (data/state.json) records the last seen accession/doc id per source,
so a re-run does not re-report the same filing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from datetime import datetime, timezone

import build_data
import fetch_13f
import fetch_politicians as fp
from common import BROWSER_UA, DATA, RAW, get, log, read_json, session, write_json

STATE = DATA / "state.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_13f(s) -> list[str]:
    changes = []
    for inst in fetch_13f.INSTITUTIONS:
        sub = fetch_13f.submissions(s, inst)
        allf = fetch_13f.filings_13f(sub)
        if not allf:
            continue
        latest = allf[-1]
        changes.append(f"{inst['person']} · 最新 13F：{latest['form']} {latest['reportDate']} 申报于 {latest['filingDate']} ({latest['accession']})")
    return changes


def check_house(s) -> list[str]:
    """Newest PTR / annual filing per member, ordered chronologically."""
    found = []
    seen = set()
    for year in fp.PEOPLE[0]["years"]:
        for r in fp.house_index(s, year):
            if r["Last"].strip().lower() != "pelosi":
                continue
            if r["FilingType"].strip() not in ("P", "O"):
                continue
            key = r["DocID"].strip()
            if key in seen:
                continue
            seen.add(key)
            found.append(
                (
                    fp._date_key(r["FilingDate"].strip()),
                    f"佩洛西 · {r['FilingType'].strip()} 文件 {key}（申报日 {r['FilingDate'].strip()}）",
                )
            )
    found.sort()
    return [msg for _, msg in found]


def check_oge(s) -> list[str]:
    """Newest OGE filing for the President (API order is not chronological)."""
    rows = sorted(fp.oge_rows(s, "Trump, Donald"), key=lambda r: r["docDate"])
    return [
        f"特朗普 · {row['docDate'][:10]} 新增 OGE 披露：{re.sub(r'<[^>]+>', '', row['type'])[:60]}"
        for row in rows
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="rebuild even when nothing changed")
    ap.add_argument("--no-fetch", action="store_true", help="only detect, do not rebuild")
    args = ap.parse_args()

    state = read_json(STATE) or {}
    log("=== daily check start ===")
    s = session(BROWSER_UA)

    try:
        sec = check_13f(s)
        house = check_house(s)
        oge = check_oge(s)
    except Exception:
        log("ERROR while checking sources:\n" + traceback.format_exc())
        return 1

    # Only the *newest* item per source is reported, and only if it differs from
    # the previous run — otherwise every day would report the same filing.
    summary = {}
    for key, items in (("sec", sec), ("house", house), ("oge", oge)):
        summary[key] = items[-1] if items else ""
    prev = state.get("lastSeen", {})
    changed = args.force or any(summary.get(k) != prev.get(k) for k in summary)

    for k, label in (("sec", "SEC 13F"), ("house", "House Clerk"), ("oge", "OGE")):
        marker = "CHANGED" if summary.get(k) != prev.get(k) else "same"
        log(f"[{label}] {marker}: {summary.get(k) or '(none)'}")

    write_json(
        STATE,
        {
            "lastRun": now_iso(),
            "lastSeen": summary,
            "changed": changed,
            "history": (state.get("history", []) + [{"at": now_iso(), "changed": changed, "summary": summary}])[-200:],
        },
    )

    if not changed:
        log("no new filings")
        return 0
    if args.no_fetch:
        log("changes detected (--no-fetch: skipping rebuild)")
        return 10

    log("changes detected -> refetch + rebuild")
    try:
        rc = fetch_13f.main()
        if rc:
            return 1
        rc = fp.main()
        if rc:
            return 1
        rc = build_data.main()
        if rc:
            return 1
    except Exception:
        log("ERROR during rebuild:\n" + traceback.format_exc())
        return 1

    write_json(
        STATE,
        {
            **(read_json(STATE) or {}),
            "lastRebuild": now_iso(),
        },
    )
    log("=== rebuild done ===")
    return 10


if __name__ == "__main__":
    sys.exit(main())
