"""Generate deterministic, rule-based change summaries ("本期变化摘要").

Design rules
------------
* Every sentence is produced by a template from numbers already computed in
  build_data.py — nothing is estimated, nothing is invented.
* Institutions: latest quarter vs previous quarter, using *share counts* for
  add/trim (value also moves with price) and *value* for size/concentration.
* Politicians: PTR / 278-T carry no holdings snapshot, so the summary reports
  the latest filing's trades and the cumulative direction over the whole
  disclosure history. Amounts stay as filed ranges — never point estimates.
* Filings whose PDF could not be parsed are reported as such instead of being
  silently treated as "no trades".
"""
from __future__ import annotations

import re

# ------------------------------------------------------------------ formatting
def money(v: float | None) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:,.2f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.1f}M"
    if a >= 1e3:
        return f"${v / 1e3:,.0f}K"
    return f"${v:,.0f}"


def pct(x: float | None, digits: int = 1) -> str:
    if x is None:
        return "—"
    return f"{x * 100:.{digits}f}%"


def signed_pct(x: float | None, digits: int = 1) -> str:
    if x is None:
        return "—"
    v = x * 100
    return f"{v:+.{digits}f}%"


def pp(x: float | None) -> str:
    """Percentage-point change, e.g. -2.2pct."""
    if x is None:
        return "—"
    return f"{x * 100:+.1f}pct"


def amt_text(t: str | None) -> str:
    """Normalize OCR'd ranges like '$5 000 001 -$25 000 000' for display only.

    Digits are taken verbatim from the filing; only separators are restored.
    """
    raw = (t or "").strip()
    if not raw:
        return "—"
    nums = re.findall(r"\d[\d,\. ]{2,}", raw)
    vals = []
    for n in nums:
        d = re.sub(r"\D", "", n)
        if d:
            vals.append(int(d))
    if len(vals) >= 2:
        lo, hi = vals[0], vals[1]
        if lo > hi:
            lo, hi = hi, lo
        return f"${lo:,} – ${hi:,}"
    if len(vals) == 1:
        return f"${vals[0]:,}"
    return raw


def disp_date(d: str | None) -> str:
    """Show filing dates as ISO; House Clerk files them as m/d/yyyy."""
    return _iso(d or "") or (d or "—")


def label(h: dict) -> str:
    """Securities label: 中文名（代码）, else filed English name + class.

    Nothing is translated when no Chinese name is known — the 13F name is shown
    verbatim so it stays verifiable against the filing.
    """
    zh = (h.get("zh") or "").strip()
    tick = (h.get("ticker") or "").strip()
    klass = (h.get("klass") or "").strip()
    put = (h.get("putCall") or "").strip()

    if zh:
        name = f"{zh}（{tick}）" if tick else zh
    else:
        name = (h.get("name") or "").strip()
        if klass and klass.upper() not in ("COM", "COMMON", "COMMON STOCK", "CL A", "CL B"):
            name = f"{name} {klass}"
        elif klass.upper() in ("CL A", "CL B"):
            name = f"{name}（{klass}）"
    if put:
        name = f"{name}（{put}）"
    return name


def join_names(items: list[dict], limit: int = 3, with_pct: bool = True) -> str:
    parts = []
    for h in items[:limit]:
        if with_pct and h.get("dSharesPct") is not None:
            parts.append(f"{label(h)}（{signed_pct(h['dSharesPct'], 0)}）")
        else:
            parts.append(label(h))
    return "、".join(parts)


# --------------------------------------------------------------- institutions
def _inst_items(q: dict, prev: dict | None) -> list[dict]:
    items: list[dict] = []

    # 1. size
    if prev:
        d = q["total"] / prev["total"] - 1 if prev["total"] else None
        items.append(
            {
                "k": "申报市值",
                "v": f"{money(q['total'])}，环比 {signed_pct(d)}（上季 {money(prev['total'])}）",
                "tone": "up" if (d or 0) > 0 else "down" if (d or 0) < 0 else "flat",
            }
        )
    else:
        items.append({"k": "申报市值", "v": f"{money(q['total'])}（本窗口首个报告期，无上季可比）", "tone": "flat"})

    # 2. position count
    if prev:
        dc = q["positions"] - prev["positions"]
        items.append(
            {
                "k": "持仓标的",
                "v": f"{q['positions']} 个（上季 {prev['positions']} 个，{dc:+d}）",
                "tone": "flat",
            }
        )
    else:
        items.append({"k": "持仓标的", "v": f"{q['positions']} 个", "tone": "flat"})

    # 3. adds / trims by share count
    adds = [h for h in q["holdings"] if h["action"] == "add" and h.get("dSharesPct") is not None]
    adds.sort(key=lambda h: -h["dSharesPct"])
    trims = [h for h in q["holdings"] if h["action"] == "trim" and h.get("dSharesPct") is not None]
    trims.sort(key=lambda h: h["dSharesPct"])
    if adds:
        items.append({"k": "加仓", "v": join_names(adds, 3), "tone": "up"})
    if trims:
        items.append({"k": "减仓", "v": join_names(trims, 3), "tone": "down"})

    # 4. new / exit by value
    new = sorted([h for h in q["holdings"] if h["action"] == "new"], key=lambda h: -h["value"])
    exits = sorted(q.get("exits", []), key=lambda h: -h["prevValue"])
    if new:
        txt = "、".join(f"{label(h)}（{money(h['value'])}，占 {pct(h['weight'])}）" for h in new[:3])
        more = f" 等 {len(new)} 个" if len(new) > 3 else ""
        items.append({"k": "新建", "v": txt + more, "tone": "up"})
    if exits:
        txt = "、".join(f"{label(h)}（上季 {money(h['prevValue'])}）" for h in exits[:3])
        more = f" 等 {len(exits)} 个" if len(exits) > 3 else ""
        items.append({"k": "清仓", "v": txt + more, "tone": "down"})

    # 5. concentration
    c, pc = q["concentration"], (prev or {}).get("concentration")
    if pc:
        items.append(
            {
                "k": "集中度",
                "v": f"Top10 {pct(c['top10'])}（上季 {pct(pc['top10'])}，{pp(c['top10'] - pc['top10'])}）；"
                f"Top1 {pct(c['top1'])}",
                "tone": "flat",
            }
        )
    else:
        items.append({"k": "集中度", "v": f"Top10 {pct(c['top10'])}；Top1 {pct(c['top1'])}", "tone": "flat"})

    # 6. largest single position move, as the "story" of the quarter
    moved = [h for h in q["holdings"] if h.get("dValue") is not None]
    if moved:
        top = max(moved, key=lambda h: abs(h["dValue"]))
        items.append(
            {
                "k": "最大单项变动",
                "v": f"{label(top)} 市值 {money(top['dValue'])}"
                f"（股数 {signed_pct(top.get('dSharesPct'), 0)}，占比 {pp(top.get('dWeight'))}）",
                "tone": "up" if top["dValue"] > 0 else "down",
            }
        )
    return items


def institution_summary(inst: dict, q: dict, prev: dict | None) -> dict:
    items = _inst_items(q, prev)
    if prev:
        d = q["total"] / prev["total"] - 1 if prev["total"] else None
        lead = f"{q['period']} 申报市值 {money(q['total'])}，环比 {signed_pct(d)}"
        acts = q.get("counts", {})
        lead += (
            f"；本季 {acts.get('new', 0)} 新建、{acts.get('add', 0)} 加仓、"
            f"{acts.get('trim', 0)} 减仓、{acts.get('exit', 0)} 清仓"
        )
    else:
        lead = f"{q['period']} 申报市值 {money(q['total'])}，本窗口首个报告期"
    return {
        "period": q["period"],
        "prevPeriod": prev["period"] if prev else None,
        "lead": lead,
        "items": items,
        "caveat": "股数变化剔除价格影响；市值与占比变化含股价涨跌。13F 仅含美国上市证券多头与期权。",
        "sourceUrl": q.get("tableUrl"),
        "sourceLabel": f"13F-HR {q['accession']} · 申报日 {q['filingDate']}",
    }


def institution_summaries(inst: dict) -> list[dict]:
    qs = inst["quarters"]
    return [institution_summary(inst, q, qs[i - 1] if i else None) for i, q in enumerate(qs)]


# ---------------------------------------------------------------- politicians
def _trades_of(trades: list[dict], filing_id: str) -> list[dict]:
    return [t for t in trades if t.get("filingId") == filing_id]


def _amount_span(trades: list[dict], kind: str) -> tuple[int, int, int]:
    """(count, low-sum, high-sum) for one direction. Ranges, so this is a band."""
    sel = [t for t in trades if t["kind"] == kind]
    lo = sum(t.get("amountLow") or 0 for t in sel)
    hi = sum(t.get("amountHigh") or 0 for t in sel)
    return len(sel), lo, hi


def _iso(d: str) -> str:
    """Filing dates arrive as ISO (OGE) or m/d/yyyy (House Clerk): normalize."""
    d = (d or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return d
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", d)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return d


def politician_summary(p: dict) -> dict:
    filings = sorted(p["filings"], key=lambda f: _iso(f["date"]))
    trades = p["trades"]
    items: list[dict] = []

    if not filings:
        return {
            "period": "—",
            "prevPeriod": None,
            "lead": "暂无披露文件",
            "items": [],
            "caveat": "",
            "sourceUrl": None,
            "sourceLabel": "",
        }

    latest = filings[-1]
    prev = filings[-2] if len(filings) > 1 else None
    lt = _trades_of(trades, latest["docId"])
    pt = _trades_of(trades, prev["docId"]) if prev else []

    # 1. the latest filing
    kind_cn = {"PTR": "PTR 定期交易报告", "278T": "OGE Form 278-T", "ANNUAL": "年度报告"}.get(
        latest["type"], latest["type"]
    )
    if latest["type"] == "ANNUAL":
        items.append(
            {
                "k": "最新披露",
                "v": f"{disp_date(latest['date'])} 年度报告（{latest['txCount']} 项持仓/收入条目）",
                "tone": "flat",
            }
        )
    else:
        items.append(
            {"k": "最新披露", "v": f"{disp_date(latest['date'])} {kind_cn}，{len(lt)} 笔交易", "tone": "flat"}
        )

    # 2. what it contained
    if lt:
        nb, nblo, nbhi = _amount_span(lt, "buy")
        ns, nslo, nshi = _amount_span(lt, "sell")
        parts = []
        if nb:
            parts.append(f"买入/行权 {nb} 笔（{money(nblo)}–{money(nbhi)}）")
        if ns:
            parts.append(f"卖出 {ns} 笔（{money(nslo)}–{money(nshi)}）")
        if parts:
            items.append({"k": "该文件内容", "v": "；".join(parts), "tone": "flat"})
        # name the biggest line items
        big = sorted(lt, key=lambda t: -(t.get("amountLow") or 0))[:3]
        named = "、".join(
            f"{t.get('ticker') or (t.get('asset') or '')[:28]}（{amt_text(t.get('amountText'))}）" for t in big
        )
        items.append({"k": "主要标的", "v": named, "tone": "flat"})
    elif latest["type"] != "ANNUAL":
        items.append(
            {
                "k": "该文件内容",
                "v": "该 PDF 为扫描件，未能自动解析出交易明细，请直接查看原始文件",
                "tone": "flat",
            }
        )

    # 3. vs previous filing
    if prev:
        delta = len(lt) - len(pt)
        items.append(
            {
                "k": "与上份披露对比",
                "v": f"上一份为 {disp_date(prev['date'])}（{len(pt)} 笔），本份 {len(lt)} 笔（{delta:+d}）",
                "tone": "flat",
            }
        )
    else:
        items.append({"k": "与上份披露对比", "v": "这是第一份披露文件", "tone": "flat"})

    # 4. cumulative direction over all disclosures
    nb, nblo, nbhi = _amount_span(trades, "buy")
    ns, nslo, nshi = _amount_span(trades, "sell")
    if trades:
        items.append(
            {
                "k": "累计方向",
                "v": f"共 {len(trades)} 笔：买入/行权 {nb} 笔（下限合计 {money(nblo)}）、"
                f"卖出 {ns} 笔（下限合计 {money(nslo)}）；净向下限 {money(nblo - nslo)}",
                "tone": "up" if nblo > nslo else "down",
            }
        )

    # 5. annual holdings snapshot, if any
    snaps = p.get("snapshots") or []
    if snaps:
        s = snaps[-1]
        top = s["top10"][:3]
        items.append(
            {
                "k": "持仓快照",
                "v": f"{disp_date(s['filedOn'])} 年度报告 {s['count']} 项，组合区间合计 "
                f"{money(s['totalLow'])}–{money(s['totalHigh'])}；前三："
                + "、".join(f"{i['name'][:26]}（{i['valueText']}）" for i in top),
                "tone": "flat",
            }
        )

    lead_bits = []
    if lt:
        nb, nblo, _ = _amount_span(lt, "buy")
        ns, nslo, _ = _amount_span(lt, "sell")
        lead_bits.append(f"{disp_date(latest['date'])} 最新披露 {len(lt)} 笔")
        if nb and ns:
            lead_bits.append(f"买入 {nb} 笔 / 卖出 {ns} 笔")
        elif nb:
            lead_bits.append(f"全部为买入/行权（{nb} 笔）")
        elif ns:
            lead_bits.append(f"全部为卖出（{ns} 笔）")
    else:
        lead_bits.append(f"{disp_date(latest['date'])} 最新披露（{kind_cn}）")

    return {
        "period": disp_date(latest["date"]),
        "prevPeriod": disp_date(prev["date"]) if prev else None,
        "lead": "，".join(lead_bits),
        "items": items,
        "caveat": "PTR / 278-T 只披露区间金额，不披露持仓快照，因此这里给的是交易流水摘要，不是持仓变化。",
        "sourceUrl": latest.get("url"),
        "sourceLabel": f"{kind_cn} · 申报日 {disp_date(latest['date'])}",
    }


# ---------------------------------------------------------------- summary page
def headline_for(kind: str, obj: dict, summary: dict) -> str:
    """One-line takeaway used on the cross-object summary page."""
    if kind == "institution":
        q = obj["quarters"][-1]
        prev = obj["quarters"][-2] if len(obj["quarters"]) > 1 else None
        d = (q["total"] / prev["total"] - 1) if prev and prev["total"] else None
        acts = q.get("counts", {})
        return (
            f"{q['period']}：{money(q['total'])}（{signed_pct(d)}），"
            f"新建 {acts.get('new', 0)} / 加仓 {acts.get('add', 0)} / "
            f"减仓 {acts.get('trim', 0)} / 清仓 {acts.get('exit', 0)}"
        )
    return summary["lead"]
