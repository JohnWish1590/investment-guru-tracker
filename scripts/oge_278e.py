"""Parse OGE Form 278e "Part 6: Other Assets and Income" into a holdings table.

Layout (per page): a numbered row carries EIF + Value + Income columns, and the
asset description sits on the line *below* the numbered row, in the left column:

    6  N/A  $1,000,001 - $5,000,000  INTEREST  $15,001 - $50,000
    COLORADO RVR TX MUN WTR DIST SYS RFDG REV B/E 5.00 % Due Jan 1, 2026

Values are ranges (OGE brackets), never exact amounts.
"""
from __future__ import annotations

import io
import re

import pdfplumber

X_NUM = (0, 32)
X_DESC = (32, 405)
X_EIF = (405, 450)
X_VAL = (450, 560)
X_INCOME = (560, 800)

RANGE = re.compile(r"\$([\d,]+)\s*-\s*\$([\d,]+)")
NONE_VAL = re.compile(r"None \(or less than \$([\d,]+)\)", re.I)
PART6 = re.compile(r"Part\s*6\s*:\s*Other Assets and Income", re.I)


def _parse_value(text: str) -> tuple[int | None, int | None, str]:
    m = RANGE.search(text)
    if m:
        return int(m.group(1).replace(",", "")), int(m.group(2).replace(",", "")), f"${m.group(1)} - ${m.group(2)}"
    m = NONE_VAL.search(text)
    if m:
        return 0, int(m.group(1).replace(",", "")), f"None (or less than ${m.group(1)})"
    return None, None, ""


def _lines(page):
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    rows: dict[int, list] = {}
    for w in words:
        key = round(w["top"] / 5)
        rows.setdefault(key, []).append(w)
    return [(k, sorted(rows[k], key=lambda w: w["x0"])) for k in sorted(rows)]


def _txt(ws, band) -> str:
    lo, hi = band
    return re.sub(r"\s+", " ", " ".join(w["text"] for w in ws if lo <= w["x0"] < hi)).strip()


def parse_278e_part6(pdf_bytes: bytes) -> list[dict]:
    items: list[dict] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        in_part6 = False
        for page in pdf.pages:
            text = page.extract_text() or ""
            if PART6.search(text):
                in_part6 = True
            elif in_part6 and re.search(r"Part\s*7\s*:", text):
                break
            if not in_part6:
                continue

            lines = _lines(page)
            for i, (_, ws) in enumerate(lines):
                num = _txt(ws, X_NUM)
                if not re.fullmatch(r"\d{1,3}", num):
                    continue
                val_txt = _txt(ws, X_VAL)
                lo, hi, label = _parse_value(val_txt)
                if label == "":
                    continue
                desc = _txt(ws, X_DESC)
                # Newer filings put the asset name on the numbered line; the older
                # layout leaves it blank there and puts it on the line below.
                if desc in ("", "Yes", "No", "N/A") and i + 1 < len(lines):
                    nxt = _txt(lines[i + 1][1], X_DESC)
                    if nxt not in ("", "Yes", "No", "N/A"):
                        desc = nxt
                if not desc:
                    continue
                inc_matches = RANGE.findall(_txt(ws, X_INCOME))
                items.append(
                    {
                        "line": int(num),
                        "description": desc,
                        "eif": _txt(ws, X_EIF),
                        "valueLow": lo,
                        "valueHigh": hi,
                        "valueText": label,
                        "incomeText": (
                            f"${inc_matches[-1][0]} - ${inc_matches[-1][1]}" if inc_matches else ""
                        ),
                        "page": page.page_number,
                    }
                )
    # dedupe: a row can be re-read when a page repeats the header, and the same
    # asset legitimately appears in several accounts (different pages).
    seen = set()
    out = []
    for it in items:
        key = (it["page"], it["line"])
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out
