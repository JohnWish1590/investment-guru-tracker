"""Parse U.S. House Clerk STOCK Act PTR PDFs (Periodic Transaction Reports).

The PDF is a fixed-layout form. Columns are located by x-position:
    owner ~55-100 | asset ~100-256 | type ~256-322 | date ~322-376
    notification ~376-442 | amount ~442-522 | cap.gains ~522+
Each transaction can spill onto following lines (wrapped asset name, "[OP]" tag,
and F/S/L/C/D notes), so lines are stitched back onto the last record.
"""
from __future__ import annotations

import re
from collections import defaultdict

import pdfplumber

BANDS = [
    ("owner", 55, 100),
    ("asset", 100, 256),
    ("type", 256, 322),
    ("date", 322, 376),
    ("notified", 376, 442),
    ("amount", 442, 522),
    ("gains", 522, 640),
]

TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)")
ATYPE = re.compile(r"\[([A-Z]{2})\]")
AMOUNT = re.compile(r"\$([\d,]+)\s*-\s*\$?([\d,]+)")
DATE = re.compile(r"\d{2}/\d{2}/\d{4}")
NULLS = re.compile(r"\x00+")


def _clean(text: str) -> str:
    """Null bytes in this PDF are used as label separators: 'D\\x00\\x00: x' -> 'D: x'."""
    return re.sub(r"\s+", " ", NULLS.sub(" ", text)).strip()


def _lines(page):
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    buckets: dict[int, list] = defaultdict(list)
    for w in words:
        buckets[round(w["top"] / 4)].append(w)
    for key in sorted(buckets):
        yield sorted(buckets[key], key=lambda w: w["x0"])


def _band(words, lo, hi):
    return _clean(" ".join(w["text"] for w in words if lo <= w["x0"] < hi))


def parse_pdf(path) -> list[dict]:
    recs: list[dict] = []
    header_seen = False
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for words in _lines(page):
                if not words:
                    continue
                parts = {name: _band(words, lo, hi) for name, lo, hi in BANDS}

                # column header row -> transactions start after it
                if "Owner" in parts["owner"] and "Asset" in parts["asset"]:
                    header_seen = True
                    continue
                if not header_seen:
                    continue
                if parts["asset"].startswith(("*", "Name:", "Status:", "State/")):
                    continue
                # the form's footnote line leaks into the notes column
                joined = " ".join(w["text"] for w in words)
                if "asset type abbreviations" in joined or "fd.house.gov" in joined:
                    continue

                is_record = bool(DATE.search(parts["date"])) and bool(parts["type"])
                if is_record:
                    recs.append(
                        {
                            "owner": parts["owner"],
                            "asset": parts["asset"],
                            "type": parts["type"],
                            "date": DATE.search(parts["date"]).group(0),
                            "notified": (DATE.search(parts["notified"]).group(0) if DATE.search(parts["notified"]) else ""),
                            "amount": parts["amount"],
                            "gains": parts["gains"],
                            "notes": [],
                            "page": page.page_number,
                        }
                    )
                    continue

                if not recs:
                    continue
                cur = recs[-1]
                asset_txt = parts["asset"]
                amount_txt = parts["amount"]

                # a note line: "F S: New" / "D: Purchased ..." / "L: US" / "C: Loss ..."
                note_like = bool(re.match(r"^[A-Z](?: [A-Z])? ?:", asset_txt))
                has_tag = bool(TICKER.search(asset_txt) or ATYPE.search(asset_txt))
                if note_like:
                    cur["notes"].append(asset_txt)
                elif asset_txt and has_tag:
                    # wrapped asset name carrying the (TICKER) / [TYPE] tag
                    cur["asset"] = (cur["asset"] + " " + asset_txt).strip()
                elif asset_txt and cur["notes"]:
                    # continuation of the last note (e.g. "a strike price of $150 ...")
                    cur["notes"][-1] = (cur["notes"][-1] + " " + asset_txt).strip()
                elif asset_txt:
                    cur["asset"] = (cur["asset"] + " " + asset_txt).strip()

                if amount_txt and "$" in amount_txt and not AMOUNT.search(cur["amount"]):
                    cur["amount"] = (cur["amount"] + " " + amount_txt).strip()
    return recs


def normalize(rec: dict, source: dict) -> dict:
    asset = rec["asset"]
    tick = TICKER.search(asset)
    atype = ATYPE.search(asset)
    name = re.sub(r"\s+", " ", TICKER.sub("", ATYPE.sub("", asset))).strip(" -")
    m = AMOUNT.search(rec["amount"].replace("\n", " "))
    note = " ".join(rec["notes"]).strip()
    # the certification block and the form footnote sit below the table: cut them off
    note = re.split(
        r"I CERTIFY|CERTIFY|asset type abbreviations|Digitally Signed|For the complete list|"
        "O S the statements|the statements I have made|Further,",
        note,
    )[0].strip()
    note = re.sub(r"\bF S ?:", "F/S:", note)
    note = re.sub(r"\b([FSDLC]) ?:", r"\1:", note)
    ttype = rec["type"].replace(" ", "")
    kind = "buy" if ttype.startswith("P") else "sell" if ttype.startswith("S") else "other"
    shares = None
    sm = re.search(r"(?:Sold|Purchased|sold|purchased)\s+([\d,]+)\s+shares", note)
    if sm:
        shares = int(sm.group(1).replace(",", ""))
    return {
        "owner": rec["owner"] or "SP",
        "asset": name,
        "ticker": tick.group(1) if tick else None,
        "assetType": atype.group(1) if atype else None,
        "txType": rec["type"],
        "kind": kind,
        "date": rec["date"],
        "notified": rec["notified"],
        "amountText": rec["amount"],
        "amountLow": int(m.group(1).replace(",", "")) if m else None,
        "amountHigh": int(m.group(2).replace(",", "")) if m else None,
        "shares": shares,
        "note": note,
        "page": rec["page"],
        "source": source,
    }
