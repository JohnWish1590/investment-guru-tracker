"""Parse U.S. House Clerk annual financial disclosure reports (filing type "O").

Unlike the PTRs, the annual report is a holdings *snapshot* with value brackets,
which is what makes a "top-10 holdings" view possible for members of Congress.

Column layout (fixed form, x-positions):
    asset 18-240 | owner 240-278 | value 278-362 | income type 362-443 | income 443+

Parsing strategy: a record starts on the line where the *Owner* column is filled
(SP / JT / DC) and continues over the following lines until the next owner line.
That handles wrapped asset names ("[RP]" tag lines), wrapped value ranges, and the
"L:" (location) / "D:" (description) footnotes uniformly.
"""
from __future__ import annotations

import re
from collections import defaultdict

import pdfplumber

X_ASSET = (18, 240)
X_OWNER = (240, 278)
X_VALUE = (278, 362)
X_INC_TYPE = (362, 443)
X_INC = (443, 800)

RANGE = re.compile(r"\$([\d,]+)\s*-\s*\$([\d,]+)")
NONE = re.compile(r"None \(or less than \$([\d,]+)\)", re.I)
BARE_NONE = re.compile(r"^None$", re.I)
HOLDINGS_HEADER = re.compile(r"Value\s+of\s+Asset", re.I)
TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)")
ATYPE = re.compile(r"\[([A-Z]{2})\]")
NULLS = re.compile(r"\x00+")
NOTE = re.compile(r"^([LDC]) ?:")
HEADER = re.compile(r"^(Asset\b|Filing\b|Name:|Status:|State/District:|Clerk\b|\*|For the complete|\$1,000\?)")
OWNER = re.compile(r"^(SP|JT|DC|SP\b.*)$")


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", NULLS.sub(" ", t)).strip()


def _lines(page):
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    rows: dict[int, list] = defaultdict(list)
    for w in words:
        rows[round(w["top"] / 4)].append(w)
    return [(k, sorted(rows[k], key=lambda w: w["x0"])) for k in sorted(rows)]


def _txt(ws, band):
    lo, hi = band
    return _clean(" ".join(w["text"] for w in ws if lo <= w["x0"] < hi))


def _value(text: str):
    m = RANGE.search(text)
    if m:
        return int(m.group(1).replace(",", "")), int(m.group(2).replace(",", "")), f"${m.group(1)} - ${m.group(2)}"
    m = NONE.search(text)
    if m:
        return 0, int(m.group(1).replace(",", "")), f"None (or less than ${m.group(1)})"
    if BARE_NONE.match(text.strip()):
        return 0, 1000, "None"
    return None, None, ""


def _groups(pdf):
    """Yield dicts of concatenated column text, one per logical disclosure row.

    Only the holdings section is read: it starts at the "Value of Asset" column
    header and ends at the transactions / spouse-income / liability sections.
    """
    cur = None
    in_holdings = False
    for page in pdf.pages:
        for _, ws in _lines(page):
            page_head = _clean(" ".join(w["text"] for w in ws))
            asset = _txt(ws, X_ASSET)
            owner = _txt(ws, X_OWNER)
            value = _txt(ws, X_VALUE)
            inc_type = _txt(ws, X_INC_TYPE)
            inc = _txt(ws, X_INC)

            if HOLDINGS_HEADER.search(page_head):
                in_holdings = True
                continue
            if not in_holdings:
                continue
            if re.search(r"Date\s+Tx\.|^Type Date Amount|^Source Type Amount|^Owner Creditor", page_head):
                in_holdings = False
                if cur:
                    yield cur
                    cur = None
                continue

            if owner and OWNER.match(owner) and not HEADER.match(asset):
                if cur:
                    yield cur
                cur = {
                    "asset": asset,
                    "owner": owner,
                    "value": value,
                    "incType": inc_type,
                    "inc": inc,
                    "notes": [],
                    "page": page.page_number,
                }
                continue
            if cur is None:
                continue
            if HEADER.match(asset) and not asset.startswith(("L:", "D:", "C:")):
                continue
            m = NOTE.match(asset)
            if m:
                cur["notes"].append((m.group(1), _clean(asset[m.end():])))
                continue
            has_tag = bool(ATYPE.search(asset) or TICKER.search(asset))
            if asset:
                if has_tag or not cur["notes"]:
                    cur["asset"] = (cur["asset"] + " " + asset).strip()
                else:
                    tag, body = cur["notes"][-1]
                    cur["notes"][-1] = (tag, (body + " " + asset).strip())
            if value and RANGE.search(cur["value"]) is None and not cur["value"].startswith("None"):
                cur["value"] = (cur["value"] + " " + value).strip()
            if inc_type and inc_type not in cur["incType"]:
                cur["incType"] = (cur["incType"] + " " + inc_type).strip()
            if inc:
                cur["inc"] = (cur["inc"] + " " + inc).strip()
    if cur:
        yield cur


def parse_annual(pdf_path) -> list[dict]:
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for g in _groups(pdf):
            lo, hi, label = _value(g["value"])
            if label == "" and not g["asset"]:
                continue
            t = TICKER.search(g["asset"])
            a = ATYPE.search(g["asset"])
            name = re.sub(r"\s+", " ", TICKER.sub("", ATYPE.sub("", g["asset"]))).strip(" -,.")
            if not name:
                continue
            location = ""
            note = ""
            for tag, body in g["notes"]:
                if tag == "L":
                    location = body
                elif tag == "D":
                    note = (note + " " + body).strip()
                else:
                    note = (note + " " + body).strip()
            out.append(
                {
                    "name": name,
                    "asset": g["asset"],
                    "owner": g["owner"],
                    "ticker": t.group(1) if t else None,
                    "assetType": a.group(1) if a else None,
                    "valueLow": lo,
                    "valueHigh": hi,
                    "valueText": label,
                    "incomeType": g["incType"],
                    "incomeText": g["inc"],
                    "location": location,
                    "note": note,
                    "page": g["page"],
                }
            )
    return out
