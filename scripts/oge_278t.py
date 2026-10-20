"""Parse OGE Form 278-T (Periodic Transaction Report) PDFs.

These PDFs are scanned-and-OCR'd, so plain text extraction is unreliable and the
layout shifts between pages: sometimes the asset description sits *above* the
numbered transaction line, sometimes the amount wraps to the next line.

Strategy: work on word coordinates with fixed x-bands, then stitch fragments:
  * a "record line" is one containing a date and a purchase/sale word
  * the description is taken from the same line, else from the nearest lines above
  * the amount is taken from the same line, else from the nearest line below
"""
from __future__ import annotations

import re
from collections import defaultdict

import pdfplumber

X_NUM = (0, 100)
X_DESC = (100, 480)
X_KIND = (480, 560)
X_DATE = (555, 625)
X_FLAG = (620, 640)
X_AMT = (636, 800)

DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
KIND = re.compile(r"^(purchase|sale|lourchase|lourchaso|lcurchaso|IDUrchaso|DUrchaso|salo|"
                  r"PUrchase|Purchase|Sale|SALE|PURCHASE|IPUrchaso|1ourchase|Durchaso)$", re.I)

BRACKETS = [
    (1001, 15000),
    (15001, 50000),
    (50001, 100000),
    (100001, 250000),
    (250001, 500000),
    (500001, 1000000),
    (1000001, 5000000),
    (5000001, 25000000),
    (25000001, 50000000),
    (50000001, 100000000),
]


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


# OCR on these scanned forms frequently swaps digits for look-alike letters.
OCR_DIGITS = str.maketrans({"o": "0", "O": "0", "l": "1", "I": "1", "i": "1",
                            "s": "5", "S": "5", "B": "8", "g": "9", "Z": "2"})


def ocr_normalize(amount_text: str) -> str:
    """Keep only $ , . digits, letters used as digits, and range separators."""
    t = re.sub(r"\s+", "", amount_text)
    t = re.sub(r"[^\d$.,\-•oOlIiSsBgZ]", "", t)
    return t.translate(OCR_DIGITS)


def bracket_from_text(amount_text: str) -> tuple[int | None, int | None]:
    d = _digits(ocr_normalize(amount_text))
    if not d:
        return None, None
    for lo, hi in BRACKETS:
        if d.startswith(f"{lo}{hi}"):
            return lo, hi
    for lo, hi in sorted(BRACKETS, key=lambda x: -len(str(x[0]))):
        if d.startswith(str(lo)):
            return lo, hi
    if len(d) <= 6:
        return int(d), int(d)
    return None, None


def _lines(page):
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    buckets: dict[int, list] = defaultdict(list)
    for w in words:
        buckets[round(w["top"] / 4)].append(w)
    for key in sorted(buckets):
        yield key, sorted(buckets[key], key=lambda w: w["x0"])


def _txt(words, band) -> str:
    lo, hi = band
    return re.sub(r"\s+", " ", " ".join(w["text"] for w in words if lo <= w["x0"] < hi)).strip()


def _clean_kind(k: str) -> str | None:
    k = k.strip()
    if KIND.match(k):
        return "buy" if k.lower().startswith(("p", "l", "i", "1", "d")) and not k.lower().startswith("s") else "sell"
    return None


def parse_278t(pdf_bytes: bytes) -> list[dict]:
    out: list[dict] = []
    with pdfplumber.open(pdf_bytes if hasattr(pdf_bytes, "read") else __import__("io").BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            lines = list(_lines(page))
            # keep only the transaction area (below the header labels)
            start = 0
            for i, (_, ws) in enumerate(lines):
                if _txt(ws, X_DESC).upper().startswith("DEACR") or "Deacrlptlon" in _txt(ws, X_DESC):
                    start = i + 1
            lines = lines[start:]

            rows = []
            for idx, (_, ws) in enumerate(lines):
                date_m = DATE.search(_txt(ws, X_DATE))
                kind = _clean_kind(_txt(ws, X_KIND))
                if not (date_m and kind):
                    continue
                num_txt = _txt(ws, X_NUM)
                num_m = re.match(r"^(\d{1,3})$", num_txt)
                desc = _txt(ws, X_DESC)
                amt = _txt(ws, X_AMT)
                rows.append(
                    {
                        "i": idx,
                        "num": int(num_m.group(1)) if num_m else None,
                        "desc": desc,
                        "kind": kind,
                        "date": date_m.group(0),
                        "flag": _txt(ws, X_FLAG),
                        "amt": amt,
                        "page": page.page_number,
                    }
                )

            for n, r in enumerate(rows):
                lo_i = rows[n - 1]["i"] if n else -1
                if not r["desc"]:
                    # description sits on the lines above the numbered line
                    for j in range(r["i"] - 1, lo_i, -1):
                        cand = _txt(lines[j][1], X_DESC)
                        if cand and not DATE.search(cand) and not KIND.match(cand):
                            r["desc"] = cand
                            break
                if not r["amt"]:
                    hi_i = rows[n + 1]["i"] if n + 1 < len(rows) else len(lines)
                    for j in range(r["i"] + 1, hi_i):
                        cand = _txt(lines[j][1], X_AMT)
                        if "$" in cand:
                            r["amt"] = cand
                            break
                if not r["desc"]:
                    continue
                lo, hi = bracket_from_text(r["amt"])
                amt_text = re.sub(r"\s+", " ", r["amt"]).strip()
                # strip OCR'd flag words that leaked into the amount column
                amt_text = re.sub(r"^(?:no|yes|yos|vos|ves|y|v|\|)\s+", "", amt_text, flags=re.I).strip()
                out.append(
                    {
                        "row": r["num"],
                        "description": r["desc"].strip(" .-"),
                        "kind": r["kind"],
                        "date": r["date"],
                        "over30days": r["flag"].lower().startswith(("y", "v")),
                        "amountText": amt_text,
                        "amountLow": lo,
                        "amountHigh": hi,
                        "page": r["page"],
                    }
                )
    return out
