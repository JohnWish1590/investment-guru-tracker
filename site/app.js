/* 投资大佬持仓追踪 — 前端逻辑
 * 数据来自 window.SITE_DATA（由 scripts/build_data.py 生成）
 * 所有数字都带原始披露文件链接，不做任何估算或补全。
 */
(() => {
  const D = window.SITE_DATA;
  if (!D) {
    document.body.insertAdjacentHTML(
      "afterbegin",
      '<div style="padding:24px;color:#f09186">未找到 site/data.js，请先运行 python scripts/build_data.py</div>'
    );
    return;
  }

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  /* ------------------------------------------------------------ formatting */
  const nf = new Intl.NumberFormat("en-US");
  const int = (n) => (n === null || n === undefined || Number.isNaN(n) ? "—" : nf.format(Math.round(n)));
  const pct = (x, digits = 2) =>
    x === null || x === undefined || Number.isNaN(x) ? "—" : (x * 100).toFixed(digits) + "%";
  const signedPct = (x, digits = 1) => {
    if (x === null || x === undefined || Number.isNaN(x)) return "—";
    const v = x * 100;
    return (v > 0 ? "+" : "") + v.toFixed(digits) + "%";
  };
  const signedInt = (n) => {
    if (n === null || n === undefined) return "—";
    return (n > 0 ? "+" : "") + nf.format(Math.round(n));
  };
  const money = (v) => {
    if (v === null || v === undefined) return "—";
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (a >= 1e3) return (v / 1e3).toFixed(0) + "K";
    return nf.format(Math.round(v));
  };
  const usd = (v) => (v === null || v === undefined ? "—" : "$" + nf.format(Math.round(v)));
  const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const shortAcc = (a) => (a || "").replace(/^0+/, "");

  const ACTION = {
    add: { label: "加仓", cls: "add" },
    trim: { label: "减仓", cls: "trim" },
    new: { label: "新建", cls: "new" },
    exit: { label: "清仓", cls: "exit" },
    hold: { label: "不变", cls: "hold" },
    first: { label: "首份", cls: "first" },
  };
  // 表格里证券名：中文优先，括号内保留 13F 原文与代码，便于核对原始文件
  const secName = (h) => {
    const zh = (h.zh || "").trim();
    const tick = (h.ticker || "").trim();
    if (!zh) return { main: h.name || "", sub: [h.klass, h.cusip].filter(Boolean).join(" · ") };
    const sub = [tick, h.klass, h.cusip].filter(Boolean).join(" · ");
    return { main: zh, sub };
  };

  const badge = (a) => {
    const m = ACTION[a] || { label: a, cls: "hold" };
    return `<span class="badge ${m.cls}">${m.label}</span>`;
  };

  const fmtDate = (iso) => (iso && iso.length >= 10 ? iso.slice(0, 10) : iso || "—");

  /* --------------------------------------------------------------- header */
  const M = D.meta;
  $("#fresh-text").textContent =
    `最新 13F 报告期 ${M.latestPeriod} · 最近申报 ${M.latestFilingDate} · 政客交易最新 ${M.lastTradeDate || "—"}`;
  $("#link-sec").href = M.sources.sec;
  $("#link-house").href = M.sources.house;
  $("#link-oge").href = M.sources.oge;
  $("#build-info").textContent =
    `数据构建时间：${M.builtAt}\nSEC 13F 数据抓取时间：${M.generatedAt}\n最新 13F 报告期：${M.latestPeriod}`;

  $("#about-srcs").innerHTML = [
    ["SEC EDGAR — 13F-HR information tables", M.sources.sec],
    ["U.S. House Clerk — 财务披露检索（PTR / 年报）", M.sources.house],
    ["U.S. Office of Government Ethics — 总统与官员披露", M.sources.oge],
  ]
    .map(([t, u]) => `<li><a href="${u}" target="_blank" rel="noopener">${esc(t)} ↗</a></li>`)
    .join("");

  /* ----------------------------------------------------------------- tabs */
  $$("#tabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$("#tabs .tab").forEach((b) => b.classList.toggle("active", b === btn));
      $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + btn.dataset.tab));
      window.scrollTo({ top: 0, behavior: "smooth" });
      setTimeout(() => Object.values(charts).forEach((c) => c && c.resize()), 60);
    });
  });

  /* ---------------------------------------------------------------- 主题 */
  // 主题只有一个真源：<html data-theme>。CSS 变量负责页面配色，
  // 图表则从这里读同名变量，因此切换主题不需要两套图表代码。
  const THEME_KEYS = {
    text: "--text",
    muted: "--muted",
    dim: "--dim",
    line: "--line",
    accent: "--accent",
    up: "--up",
    down: "--down",
    grid: "--chart-grid",
    other: "--chart-other",
    ring: "--chart-ring",
    tooltipBg: "--tooltip-bg",
    tooltipText: "--tooltip-text",
    segActive: "--seg-active-bg",
  };

  const THEME = { palette: [] };

  function readTheme() {
    const cs = getComputedStyle(document.documentElement);
    for (const [k, varName] of Object.entries(THEME_KEYS)) {
      THEME[k] = cs.getPropertyValue(varName).trim();
    }
    const pal = cs.getPropertyValue("--chart-palette").trim();
    THEME.palette = pal.split(",").map((x) => x.trim()).filter(Boolean);
  }
  readTheme();

  const currentPref = () => {
    try {
      return localStorage.getItem("igt-theme") || "auto";
    } catch (e) {
      return "auto";
    }
  };
  const systemDark = () => window.matchMedia("(prefers-color-scheme: dark)").matches;
  const resolveTheme = (pref) => (pref === "auto" ? (systemDark() ? "dark" : "light") : pref);

  /* ---------------------------------------------------------------- charts */
  const charts = {};
  const baseOpt = (extra = {}) => ({
    backgroundColor: "transparent",
    textStyle: { fontFamily: "inherit", color: THEME.text },
    animationDuration: 500,
    ...extra,
  });
  const mk = (id) => {
    const el = document.getElementById(id);
    if (!el) return null;
    const c = echarts.init(el, null, { renderer: "canvas" });
    charts[id] = c;
    return c;
  };
  const tooltip = (extra = {}) => ({
    backgroundColor: THEME.tooltipBg,
    borderColor: THEME.line,
    borderWidth: 1,
    textStyle: { color: THEME.tooltipText, fontSize: 12 },
    ...extra,
  });
  window.addEventListener("resize", () => Object.values(charts).forEach((c) => c && c.resize()));

  /* ============================================================ 摘要渲染 */
  // 摘要由 scripts/summary.py 用规则模板生成，这里只负责呈现，不再做任何计算。
  function briefHTML(sm, opts = {}) {
    if (!sm) return "";
    const tone = (t) => (t === "up" ? "up" : t === "down" ? "down" : "");
    const items = (sm.items || [])
      .map(
        (it) =>
          `<li><span class="k">${esc(it.k)}</span><span class="v ${tone(it.tone)}">${esc(it.v)}</span></li>`
      )
      .join("");
    const src = sm.sourceUrl
      ? `<a href="${sm.sourceUrl}" target="_blank" rel="noopener">${esc(sm.sourceLabel)} ↗</a>`
      : esc(sm.sourceLabel || "");
    const range = sm.prevPeriod
      ? `${esc(sm.period)} <span class="span">对比 ${esc(sm.prevPeriod)}</span>`
      : `${esc(sm.period)} <span class="span">首期</span>`;
    return `
      <div class="brief">
        ${opts.title ? `<div class="brief-head"><span class="brief-title">${opts.title}</span><span class="hint">${range}</span></div>` : ""}
        <p class="brief-lead"><b>${esc(sm.lead)}</b></p>
        <ul class="brief-items">${items}</ul>
        <div class="brief-foot">
          <span class="brief-caveat">${esc(sm.caveat || "")}</span>
          <span class="brief-src">${src}</span>
        </div>
      </div>`;
  }

  // 汇总页：5 个人/机构各一段，并排展示
  function renderBriefs() {
    const list = D.briefs || [];
    const host = $("#brief-list");
    if (!host) return;
    $("#brief-meta").innerHTML = `构建时间 ${esc(M.builtAt)}<br/>13F 最新报告期 ${esc(M.latestPeriod)}`;
    host.innerHTML =
      list
        .map((b) => {
          const sm = b.summary;
          const rows = (sm.items || [])
            .map(
              (it) =>
                `<li><span class="k">${esc(it.k)}</span><span class="v ${
                  it.tone === "up" ? "up" : it.tone === "down" ? "down" : ""
                }">${esc(it.v)}</span></li>`
            )
            .join("");
          const src = sm.sourceUrl
            ? `<a href="${sm.sourceUrl}" target="_blank" rel="noopener">${esc(sm.sourceLabel)} ↗</a>`
            : esc(sm.sourceLabel || "");
          return `
        <div class="brief" style="border-left-color:${b.color}">
          <div class="brief-head">
            <div class="who-line">
              <span class="swatch" style="background:${b.color}"></span>
              <span class="nm">${esc(b.person)}</span>
              <span class="role">${esc(b.filer)} · ${esc(b.role)}</span>
            </div>
            <span class="hint">${esc(sm.period)}${sm.prevPeriod ? " 对比 " + esc(sm.prevPeriod) : " 首期"}</span>
          </div>
          <div class="headline">${esc(b.headline)}</div>
          <ul class="brief-items">${rows}</ul>
          <div class="brief-foot">
            <span class="brief-caveat">${esc(sm.caveat || "")}</span>
            <span class="brief-src">${src}</span>
          </div>
        </div>`;
        })
        .join("") || `<div class="empty">暂无摘要</div>`;
  }

  /* ================================================================ 机构持仓 */
  const INST = D.institutions;
  let curInst = INST[0].id;
  let curQ = null;

  const getInst = (id) => INST.find((i) => i.id === id);
  const getQ = (inst, period) => inst.quarters.find((q) => q.period === period) || inst.quarters.at(-1);

  function renderInstPick() {
    $("#inst-pick").innerHTML = INST.map(
      (i) => `
      <button class="pick ${i.id === curInst ? "active" : ""}" data-inst="${i.id}">
        <span class="swatch" style="background:${i.color}"></span>
        <span>
          <span class="who">${esc(i.person)}</span><br />
          <span class="what">${esc(i.filer)}</span>
        </span>
      </button>`
    ).join("");
    $$("#inst-pick .pick").forEach((b) =>
      b.addEventListener("click", () => {
        curInst = b.dataset.inst;
        curQ = null;
        renderInstPick();
        renderInst();
      })
    );
  }

  function renderInst() {
    const inst = getInst(curInst);
    const q = getQ(inst, curQ);
    curQ = q.period;
    const prev = inst.quarters[inst.quarters.indexOf(q) - 1];

    $("#inst-name").textContent = inst.person;
    $("#inst-role").textContent = `${inst.role} · CIK ${shortAcc(inst.cik)}`;
    $("#inst-note").innerHTML = `${esc(inst.note)} <a href="${inst.edgarUrl}" target="_blank" rel="noopener">EDGAR 申报列表 ↗</a>`;

    const sel = $("#q-select");
    sel.innerHTML = inst.quarters
      .slice()
      .reverse()
      .map((x) => `<option value="${x.period}" ${x.period === q.period ? "selected" : ""}>${x.period}（期末 ${x.reportDate}）</option>`)
      .join("");
    sel.onchange = () => {
      curQ = sel.value;
      renderInst();
    };

    const amend = q.amendments.length
      ? `<span style="color:#b58fd8">含 ${q.amendments.length} 份修订（${q.amendments.map((a) => a.form + " " + a.filingDate).join("、")}）</span>`
      : "无修订";
    $("#q-meta").innerHTML = `申报日 ${q.filingDate} · ${esc(q.form)}<br />${amend}<br />
      <a href="${q.filingIndexUrl}" target="_blank" rel="noopener">filing index ↗</a> ·
      <a href="${q.primaryDocUrl}" target="_blank" rel="noopener">primary_doc.xml ↗</a>`;

    /* KPIs */
    const dTotal = prev ? q.total - prev.total : null;
    const dTotalPct = prev && prev.total ? q.total / prev.total - 1 : null;
    const c = q.counts || {};
    $("#kpis").innerHTML = `
      <div class="kpi"><div class="k">申报总市值</div><div class="v">$${money(q.total)}</div>
        <div class="d ${cls(dTotal)}">${prev ? `环比 ${signedPct(dTotalPct)}` : "首个报告期"}</div></div>
      <div class="kpi"><div class="k">持仓标的数</div><div class="v">${q.positions}</div>
        <div class="d">${prev ? `上季 ${prev.positions}` : "—"}</div></div>
      <div class="kpi"><div class="k">Top10 集中度</div><div class="v">${pct(q.concentration.top10, 1)}</div>
        <div class="d">Top1 ${pct(q.concentration.top1, 1)} · Top5 ${pct(q.concentration.top5, 1)}</div></div>
      <div class="kpi"><div class="k">本季动作</div><div class="v">${(c.add || 0) + (c.new || 0)} 加 / ${(c.trim || 0) + (c.exit || 0)} 减</div>
        <div class="d">新建 ${c.new || 0} · 清仓 ${c.exit || 0} · 不变 ${c.hold || 0}</div></div>`;

    // 该季度对应的「vs 上季」摘要（与季度选择器联动）
    const sm = (inst.summaries || []).find((x) => x.period === q.period) || (inst.summaries || []).at(-1);
    const host = $("#inst-brief");
    if (host) host.innerHTML = briefHTML(sm, { title: "本期变化摘要" });

    renderPie(inst, q);
    renderMoves(inst, q);
    renderTrend(inst, q);
    renderActions(inst, q);
    renderHoldingsTable(inst, q);
    renderMovers(inst, q);
  }

  function renderPie(inst, q) {
    const c = mk("pie");
    if (!c) return;
    const top = q.top10.slice();
    const rest = q.total - top.reduce((s, h) => s + h["value"], 0);
    const data = top.map((h) => ({
      name: h.name,
      value: h["value"],
      weight: h.weight,
      itemStyle: { color: undefined },
    }));
    if (rest > 0) data.push({ name: "其他持仓", value: rest, weight: rest / q.total, itemStyle: { color: THEME.other } });

    c.setOption(
      baseOpt({
        color: [...THEME.palette, THEME.other],
        tooltip: tooltip({
          formatter: (p) =>
            `<b>${esc(p.name)}</b><br/>市值 ${usd(p.value)}<br/>占比 ${pct(p.data.weight)}`,
        }),
        legend: {
          type: "scroll",
          orient: "vertical",
          right: 0,
          top: 6,
          bottom: 6,
          itemWidth: 9,
          itemHeight: 9,
          textStyle: { color: THEME.muted, fontSize: 11 },
          formatter: (n) => (n.length > 22 ? n.slice(0, 21) + "…" : n),
        },
        series: [
          {
            type: "pie",
            radius: ["42%", "72%"],
            center: ["34%", "52%"],
            avoidLabelOverlap: true,
            itemStyle: { borderColor: THEME.ring, borderWidth: 2 },
            label: { show: false },
            emphasis: {
              label: {
                show: true,
                formatter: (p) => `${pct(p.data.weight, 1)}`,
                fontSize: 15,
                fontWeight: 600,
                color: THEME.text,
                position: "center",
              },
              itemStyle: { shadowBlur: 12, shadowColor: "rgba(0,0,0,.5)" },
            },
            data,
          },
        ],
      })
    );
  }

  function renderMoves(inst, q) {
    const c = mk("moves");
    if (!c) return;
    const rows = q.holdings
      .filter((h) => h.dSharesPct !== null && h.dSharesPct !== undefined)
      .sort((a, b) => Math.abs(b.dSharesPct) - Math.abs(a.dSharesPct))
      .slice(0, 12)
      .reverse();
    if (!rows.length) {
      c.clear();
      c.setOption(
        baseOpt({
          title: { text: "该季度无上一季可比数据", left: "center", top: "middle", textStyle: { color: THEME.dim, fontSize: 13, fontWeight: 400 } },
        })
      );
      return;
    }
    // One position can multiply 7x and flatten everything else. Clip the bars at a
    // readable bound and keep the true value in the label + tooltip.
    const CAP = 1.2;
    const clipped = rows.some((h) => Math.abs(h.dSharesPct) > CAP);
    c.setOption(
      baseOpt({
        grid: { left: 8, right: 62, top: 12, bottom: 8, containLabel: true },
        tooltip: tooltip({
          formatter: (p) => {
            const h = rows[p.dataIndex];
            return `<b>${esc(h.name)}</b>${h.klass ? " " + esc(h.klass) : ""}<br/>股数变化 ${signedPct(h.dSharesPct)}（${signedInt(h.dShares)} 股）<br/>市值 ${usd(h["value"])}`;
          },
        }),
        xAxis: {
          type: "value",
          min: -CAP,
          max: CAP,
          axisLabel: { color: THEME.dim, formatter: (v) => (v * 100).toFixed(0) + "%" },
          splitLine: { lineStyle: { color: THEME.grid } },
        },
        yAxis: {
          type: "category",
          data: rows.map((h) => (h.name.length > 20 ? h.name.slice(0, 19) + "…" : h.name)),
          axisLabel: { color: THEME.muted, fontSize: 11 },
          axisLine: { lineStyle: { color: THEME.line } },
        },
        series: [
          {
            type: "bar",
            data: rows.map((h) => {
              const over = Math.abs(h.dSharesPct) > CAP;
              const v = over ? Math.sign(h.dSharesPct) * CAP : h.dSharesPct;
              return {
                value: v,
                trueValue: h.dSharesPct,
                over,
                itemStyle: { color: h.dSharesPct >= 0 ? THEME.up : THEME.down, borderRadius: [0, 3, 3, 0] },
              };
            }),
            barMaxWidth: 13,
            label: {
              show: true,
              position: "right",
              color: THEME.muted,
              fontSize: 10.5,
              formatter: (p) => signedPct(p.data.trueValue, 0) + (p.data.over ? " ›" : ""),
            },
          },
        ],
        graphic: clipped
          ? [
              {
                type: "text",
                right: 4,
                bottom: 0,
                style: { text: `柱长已裁到 ±${CAP * 100}%，标签为真实幅度`, fill: THEME.dim, fontSize: 10 },
              },
            ]
          : [],
      })
    );
  }

  function renderTrend(inst, q) {
    const c = mk("trend");
    if (!c) return;
    const qs = inst.quarters;
    c.setOption(
      baseOpt({
        grid: { left: 10, right: 18, top: 24, bottom: 8, containLabel: true },
        tooltip: tooltip({
          trigger: "axis",
          formatter: (ps) => {
            const i = ps[0].dataIndex;
            const x = qs[i];
            return `<b>${x.period}</b>（期末 ${x.reportDate}）<br/>申报市值 $${money(x.total)}<br/>持仓数 ${x.positions}<br/>Top10 ${pct(x.concentration.top10, 1)}`;
          },
        }),
        xAxis: {
          type: "category",
          data: qs.map((x) => x.period),
          axisLabel: { color: THEME.dim, fontSize: 11 },
          axisLine: { lineStyle: { color: THEME.line } },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: THEME.dim, formatter: (v) => "$" + money(v) },
          splitLine: { lineStyle: { color: THEME.grid } },
        },
        series: [
          {
            type: "line",
            smooth: 0.25,
            symbolSize: 6,
            data: qs.map((x) => x.total),
            lineStyle: { color: inst.color, width: 2.5 },
            itemStyle: { color: inst.color },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: inst.color + "55" },
                { offset: 1, color: inst.color + "00" },
              ]),
            },
            markPoint: {
              symbolSize: 0,
              data: [{ coord: [q.period, q.total], value: "" }],
              label: { show: false },
            },
          },
        ],
      })
    );
  }

  function renderActions(inst, q) {
    const c = mk("actions");
    if (!c) return;
    const cc = q.counts || {};
    const cats = ["新建", "加仓", "不变", "减仓", "清仓"];
    const vals = [cc.new || 0, cc.add || 0, cc.hold || 0, cc.trim || 0, cc.exit || 0];
    const cols = ["#3884be", "#2f9e6f", "#5b6773", "#c0483f", "#963c3c"];
    $("#action-hint").textContent = `共 ${q.positions} 个标的${q.exits.length ? ` · 上季清仓 ${q.exits.length}` : ""}`;
    c.setOption(
      baseOpt({
        grid: { left: 8, right: 18, top: 24, bottom: 8, containLabel: true },
        tooltip: tooltip({ trigger: "axis", axisPointer: { type: "shadow" } }),
        xAxis: {
          type: "category",
          data: cats,
          axisLabel: { color: THEME.muted, fontSize: 12 },
          axisLine: { lineStyle: { color: THEME.line } },
        },
        yAxis: {
          type: "value",
          minInterval: 1,
          axisLabel: { color: THEME.dim },
          splitLine: { lineStyle: { color: THEME.grid } },
        },
        series: [
          {
            type: "bar",
            barMaxWidth: 46,
            data: vals.map((v, i) => ({ value: v, itemStyle: { color: cols[i], borderRadius: [4, 4, 0, 0] } })),
            label: { show: true, position: "top", color: THEME.muted, fontSize: 12 },
          },
        ],
      })
    );
  }

  function renderHoldingsTable(inst, q) {
    const all = $("#show-all").checked;
    const rows = all ? q.holdings : q.top10;
    $("#table-hint").textContent = all ? `全部 ${q.positions} 个标的` : "Top 10（勾选右侧显示全部）";
    const srcCell = (h) => {
      const put = h.putCall ? `<span class="put">${esc(h.putCall)}</span>` : "";
      const links = (q.docs || [])
        .map(
          (d) =>
            `<a href="${d.tableUrl}" target="_blank" rel="noopener" title="${esc(d.form)} ${d.filingDate}">${esc(d.form)} ${esc(d.accession)}</a>`
        )
        .join("<br/>");
      return `<div class="src">${links}<br/><span class="acc">申报日 ${q.filingDate}</span>${put}</div>`;
    };
    $("#holdings-table tbody").innerHTML =
      rows
        .map(
          (h, i) => `
        <tr>
          <td class="num">${i + 1}</td>
          <td>${(() => { const n = secName(h); return `<span class="tick">${esc(n.main)}</span> <span class="klass">${esc(n.sub)}</span>`; })()}</td>
          <td class="r">${int(h.shares)}</td>
          <td class="r">${usd(h["value"])}</td>
          <td class="r">${pct(h.weight)}</td>
          <td class="r ${cls(h.dShares)}">${h.dShares === null ? "—" : `${signedInt(h.dShares)}<br/><span class="klass">${signedPct(h.dSharesPct)}</span>`}</td>
          <td class="r ${cls(h.dWeight)}">${h.dWeight === null ? "—" : signedPct(h.dWeight, 2)}</td>
          <td>${badge(h.action)}</td>
          <td>${srcCell(h)}</td>
        </tr>`
        )
        .join("") || `<tr><td colspan="9" class="empty">无数据</td></tr>`;
  }

  function renderMovers(inst, q) {
    const mv = q.movers || {};
    const line = (h, right) => `
      <li>
        <span class="l"><span class="nm">${esc(secName(h).main)}</span>
          <span class="sub2"> ${esc(secName(h).sub)}${h.putCall ? " · " + esc(h.putCall) : ""}</span><br/>
          <span class="sub2">${right}</span></span>
        <span class="r">${badge(h.action)}<br/><span class="${cls(h.dShares)}">${signedPct(h.dSharesPct)}</span></span>
      </li>`;
    const up = [...(mv.new || []).map((h) => ({ h, t: "new" })), ...(mv.adds || []).map((h) => ({ h, t: "add" }))]
      .sort((a, b) => (b.h["value"] || 0) - (a.h["value"] || 0))
      .slice(0, 7);
    const down = [...(mv.trims || []).map((h) => ({ h, t: "trim" })), ...(mv.exits || []).map((h) => ({ h, t: "exit" }))]
      .sort((a, b) => (b.h["prevValue"] || 0) - (a.h["prevValue"] || 0))
      .slice(0, 7);
    $("#movers-up").innerHTML =
      up
        .map(({ h, t }) =>
          line(
            h,
            t === "new"
              ? `新建 ${int(h.shares)} 股 · ${usd(h["value"])}`
              : `${int(h.prevShares)} → ${int(h.shares)} 股 · ${usd(h["value"])}`
          )
        )
        .join("") || `<li class="empty">该季度无加仓或新建</li>`;
    $("#movers-down").innerHTML =
      down
        .map(({ h, t }) =>
          line(
            h,
            t === "exit"
              ? `清仓（上季 ${int(h.prevShares)} 股 / ${usd(h.prevValue)}）`
              : `${int(h.prevShares)} → ${int(h.shares)} 股 · 剩 ${usd(h["value"])}`
          )
        )
        .join("") || `<li class="empty">该季度无减仓或清仓</li>`;
  }

  $("#show-all").addEventListener("change", () => renderInst());

  /* ================================================================ 政客交易 */
  const POL = D.politicians || [];
  let curPol = POL[0] ? POL[0].id : null;
  let polRange = "0";
  let tradeFilter = "all";

  function renderPolPick() {
    $("#pol-pick").innerHTML = POL.map(
      (p) => `
      <button class="pick ${p.id === curPol ? "active" : ""}" data-pol="${p.id}">
        <span class="swatch" style="background:${p.color}"></span>
        <span><span class="who">${esc(p.person)}</span><br/><span class="what">${esc(p.role)}</span></span>
      </button>`
    ).join("");
    $$("#pol-pick .pick").forEach((b) =>
      b.addEventListener("click", () => {
        curPol = b.dataset.pol;
        renderPolPick();
        renderPol();
      })
    );
  }

  function cutoffISO(months) {
    if (!months) return "0000-00-00";
    const d = new Date(M.lastTradeDate || new Date().toISOString().slice(0, 10));
    d.setMonth(d.getMonth() - months);
    return d.toISOString().slice(0, 10);
  }

  function renderPol() {
    const p = POL.find((x) => x.id === curPol);
    if (!p) return;
    const cut = cutoffISO(polRange === "0" ? 0 : Number(polRange));
    const trades = p.trades.filter((t) => t.iso >= cut);

    $("#pol-name").textContent = p.person;
    $("#pol-role").textContent = p.role;
    $("#pol-note").textContent = p.note;

    const nBuy = trades.filter((t) => t.kind === "buy").length;
    const nSell = trades.filter((t) => t.kind === "sell").length;
    const buyLow = trades.filter((t) => t.kind === "buy").reduce((s, t) => s + (t.amountLow || 0), 0);
    const sellLow = trades.filter((t) => t.kind === "sell").reduce((s, t) => s + (t.amountLow || 0), 0);
    const filed = p.filings.filter((f) => (f.date || "") >= cut);

    $("#pol-meta").innerHTML = `共 ${p.filings.length} 份披露文件 · ${p.trades.length} 笔交易<br/>最近申报 ${p.filings.at(-1)?.date || "—"}`;

    $("#pol-kpis").innerHTML = `
      <div class="kpi"><div class="k">申报笔数</div><div class="v">${trades.length}</div>
        <div class="d">买入/行权 ${nBuy} · 卖出 ${nSell}</div></div>
      <div class="kpi"><div class="k">买入金额下限合计</div><div class="v">$${money(buyLow)}</div>
        <div class="d">按披露区间下限累加</div></div>
      <div class="kpi"><div class="k">卖出金额下限合计</div><div class="v">$${money(sellLow)}</div>
        <div class="d">按披露区间下限累加</div></div>
      <div class="kpi"><div class="k">披露文件</div><div class="v">${filed.length}</div>
        <div class="d">${esc(filed.at(-1)?.type || "—")} · ${esc(filed.at(-1)?.date || "—")}</div></div>`;

    const sm = (p.summaries || [])[0];
    const host = $("#pol-brief");
    if (host) host.innerHTML = briefHTML(sm, { title: "本期变化摘要（交易流水口径）" });

    renderPolBars(trades);
    renderPolTop(trades);
    renderSnapshot(p);
    renderTrades(p, trades);
  }

  function renderPolBars(trades) {
    const c = mk("pol-bars");
    if (!c) return;
    const months = {};
    trades.forEach((t) => {
      const m = (t.iso || "").slice(0, 7);
      if (!m) return;
      const o = (months[m] = months[m] || { buy: 0, sell: 0, nb: 0, ns: 0 });
      if (t.kind === "buy") {
        o.buy += t.amountLow || 0;
        o.nb++;
      } else if (t.kind === "sell") {
        o.sell += t.amountLow || 0;
        o.ns++;
      }
    });
    const keys = Object.keys(months).sort();
    if (!keys.length) {
      c.clear();
      c.setOption(baseOpt({ title: { text: "所选范围内无交易", left: "center", top: "middle", textStyle: { color: THEME.dim, fontSize: 13, fontWeight: 400 } } }));
      return;
    }
    c.setOption(
      baseOpt({
        grid: { left: 10, right: 16, top: 30, bottom: 8, containLabel: true },
        tooltip: tooltip({
          trigger: "axis",
          axisPointer: { type: "shadow" },
          formatter: (ps) => {
            const m = ps[0].axisValue;
            const o = months[m];
            return `<b>${m}</b><br/>买入 $${money(o.buy)}（${o.nb} 笔）<br/>卖出 $${money(o.sell)}（${o.ns} 笔）`;
          },
        }),
        legend: { data: ["买入 / 行权", "卖出"], textStyle: { color: THEME.muted, fontSize: 11 }, right: 0, top: 0 },
        xAxis: {
          type: "category",
          data: keys,
          axisLabel: { color: THEME.dim, fontSize: 10.5, rotate: keys.length > 14 ? 45 : 0 },
          axisLine: { lineStyle: { color: THEME.line } },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: THEME.dim, formatter: (v) => "$" + money(v) },
          splitLine: { lineStyle: { color: THEME.grid } },
        },
        series: [
          { name: "买入 / 行权", type: "bar", stack: "x", data: keys.map((k) => months[k].buy), itemStyle: { color: THEME.up }, barMaxWidth: 26 },
          { name: "卖出", type: "bar", stack: "x", data: keys.map((k) => -months[k].sell), itemStyle: { color: THEME.down }, barMaxWidth: 26 },
        ],
      })
    );
  }

  function renderPolTop(trades) {
    const c = mk("pol-top");
    if (!c) return;
    const agg = {};
    trades.forEach((t) => {
      const k = t.ticker || t.asset;
      const o = (agg[k] = agg[k] || { label: t.ticker || t.asset, buy: 0, sell: 0, n: 0 });
      o.n++;
      if (t.kind === "buy") o.buy += t.amountLow || 0;
      else if (t.kind === "sell") o.sell += t.amountLow || 0;
    });
    const rows = Object.values(agg)
      .sort((a, b) => b.buy + b.sell - (a.buy + a.sell))
      .slice(0, 12)
      .reverse();
    if (!rows.length) {
      c.clear();
      c.setOption(baseOpt({ title: { text: "所选范围内无交易", left: "center", top: "middle", textStyle: { color: THEME.dim, fontSize: 13, fontWeight: 400 } } }));
      return;
    }
    // OGE 278-T descriptions are full municipal-bond names; they must be clipped or
    // they push the plot area off-screen.
    const shortLabel = (s) => {
      const t = String(s || "").replace(/\s+/g, " ").trim();
      return t.length > 26 ? t.slice(0, 25) + "…" : t;
    };
    c.setOption(
      baseOpt({
        grid: { left: 8, right: 70, top: 30, bottom: 8, containLabel: true },
        tooltip: tooltip({
          trigger: "axis",
          axisPointer: { type: "shadow" },
          formatter: (ps) => {
            const r = rows[ps[0].dataIndex];
            return `<b>${esc(r.label)}</b><br/>买入 $${money(r.buy)}<br/>卖出 $${money(r.sell)}<br/>${r.n} 笔`;
          },
        }),
        legend: { data: ["买入 / 行权", "卖出"], textStyle: { color: THEME.muted, fontSize: 11 }, right: 0, top: 0 },
        xAxis: {
          type: "value",
          axisLabel: { color: THEME.dim, formatter: (v) => "$" + money(v) },
          splitLine: { lineStyle: { color: THEME.grid } },
        },
        yAxis: {
          type: "category",
          data: rows.map((r) => shortLabel(r.label)),
          axisLabel: { color: THEME.muted, fontSize: 11, width: 168, overflow: "truncate" },
          axisLine: { lineStyle: { color: THEME.line } },
        },
        series: [
          { name: "买入 / 行权", type: "bar", stack: "y", data: rows.map((r) => r.buy), itemStyle: { color: THEME.up }, barMaxWidth: 12 },
          { name: "卖出", type: "bar", stack: "y", data: rows.map((r) => -r.sell), itemStyle: { color: THEME.down }, barMaxWidth: 12 },
        ],
      })
    );
  }

  function renderSnapshot(p) {
    const snap = (p.snapshots || []).at(-1);
    const card = $("#snapshot-card");
    if (!snap) {
      card.style.display = "none";
      return;
    }
    card.style.display = "";
    $("#snapshot-hint").innerHTML = `${esc(snap.label)} · 申报日 ${esc(snap.filedOn)} · 共 ${snap.count} 项 ·
      <a href="${snap.url}" target="_blank" rel="noopener">原始 PDF ↗</a>`;

    const c = mk("pol-snap-chart");
    const items = snap.top10;
    c.setOption(
      baseOpt({
        grid: { left: 8, right: 60, top: 10, bottom: 8, containLabel: true },
        tooltip: tooltip({
          formatter: (x) => {
            const it = items[x.dataIndex];
            return `<b>${esc(it.name)}</b><br/>申报价值 ${esc(it.valueText)}<br/>占披露组合 ${pct(it.weight, 1)}`;
          },
        }),
        xAxis: {
          type: "value",
          axisLabel: { color: THEME.dim, formatter: (v) => "$" + money(v) },
          splitLine: { lineStyle: { color: THEME.grid } },
        },
        yAxis: {
          type: "category",
          data: items.map((i) => (i.name.length > 22 ? i.name.slice(0, 21) + "…" : i.name)).reverse(),
          axisLabel: { color: THEME.muted, fontSize: 11 },
          axisLine: { lineStyle: { color: THEME.line } },
        },
        series: [
          {
            type: "bar",
            data: items.map((i) => i.valueLow).reverse(),
            barMaxWidth: 14,
            itemStyle: { color: p.color, borderRadius: [0, 3, 3, 0] },
            label: { show: true, position: "right", color: THEME.muted, fontSize: 10.5, formatter: (x) => "$" + money(x.value) },
          },
        ],
      })
    );

    $("#snap-table tbody").innerHTML = items
      .map(
        (it, i) => `
      <tr>
        <td class="num">${i + 1}</td>
        <td><span class="tick">${esc(it.name)}</span>
          <span class="klass">${esc(it.assetType || "")}${it.ticker ? " · " + esc(it.ticker) : ""}</span></td>
        <td class="r">${esc(it.valueText)}</td>
        <td class="r">${pct(it.weight, 1)}</td>
        <td class="src"><a href="${snap.url}" target="_blank" rel="noopener">${esc(snap.docId)}</a>
          <br/><span class="acc">p.${esc(it.page || "—")}</span></td>
      </tr>`
      )
      .join("");
  }

  function renderTrades(p, trades) {
    const shown = tradeFilter === "all" ? trades : trades.filter((t) => t.kind === tradeFilter);
    const ordered = shown.slice().reverse();
    $("#trades-hint").textContent = `${ordered.length} 笔`;
    $("#trades-table tbody").innerHTML =
      ordered
        .map((t) => {
          const kindCls = t.kind === "buy" ? "buy" : t.kind === "sell" ? "sell" : "other";
          const kindTxt = t.kind === "buy" ? "买入/行权" : t.kind === "sell" ? "卖出" : "其他";
          return `<tr>
            <td>${esc(fmtDate(t.iso))}</td>
            <td><span class="tick">${esc(t.ticker || t.asset)}</span>
              <span class="klass">${esc(t.asset)}${t.assetType ? " [" + esc(t.assetType) + "]" : ""}</span>
              ${t.note ? `<br/><span class="klass">${esc(t.note).slice(0, 110)}</span>` : ""}</td>
            <td>${esc(t.txType || "")}</td>
            <td><span class="badge ${kindCls}">${kindTxt}</span></td>
            <td class="r">${esc(t.amountText || "—")}</td>
            <td>${esc(fmtDate(t.filedOn))}</td>
            <td class="src"><a href="${t.filingUrl}" target="_blank" rel="noopener">${esc(t.filingId)}</a>
              <br/><span class="acc">p.${esc(t.page || "—")}</span></td>
          </tr>`;
        })
        .join("") || `<tr><td colspan="7" class="empty">所选范围内无交易</td></tr>`;
  }

  $("#pol-range").addEventListener("change", (e) => {
    polRange = e.target.value;
    renderPol();
  });
  $("#trade-filter").addEventListener("change", (e) => {
    tradeFilter = e.target.value;
    const p = POL.find((x) => x.id === curPol);
    const cut = cutoffISO(polRange === "0" ? 0 : Number(polRange));
    renderTrades(p, p.trades.filter((t) => t.iso >= cut));
  });

  /* ================================================================ 共同持仓 */
  const CROSS = D.cross || [];

  function renderCross() {
    const sel = $("#cross-q");
    if (!CROSS.length) {
      $("#cross-hint").textContent = "暂无重叠持仓数据";
      return;
    }
    sel.innerHTML = CROSS.slice()
      .reverse()
      .map((c) => `<option value="${c.period}">${c.period}</option>`)
      .join("");
    const draw = () => {
      const c = CROSS.find((x) => x.period === sel.value) || CROSS.at(-1);
      const heads = c.present
        .map((p) => `<th class="r" style="color:${p.color}">${esc(p.person)}<br/><span class="hint">股数 / 占比 / 动作</span></th>`)
        .join("");
      $("#cross-table thead").innerHTML = `<tr><th>标的</th><th class="num">持有家数</th>${heads}</tr>`;
      $("#cross-hint").textContent = `${c.rows.length} 个标的同时被至少两家持有 · 期末 ${c.period}`;
      $("#cross-meta").innerHTML = c.present
        .map((p) => `${esc(p.person)}：$${money(p.total)} / ${p.positions} 个标的`)
        .join("<br/>");
      $("#cross-table tbody").innerHTML = c.rows
        .slice(0, 120)
        .map((r) => {
          const cells = c.present
            .map((p) => {
              const h = r.byInst[p.id];
              if (!h) return `<td class="r"><span class="klass">—</span></td>`;
              return `<td class="r">${int(h.shares)}
                <span class="klass">${pct(h.weight, 2)}</span>
                <br/>${badge(h.action)} <span class="${cls(h.dSharesPct)}">${signedPct(h.dSharesPct, 0)}</span></td>`;
            })
            .join("");
          return `<tr>
            <td><span class="tick">${esc(r.zh || r.name)}</span>
              <span class="klass">${esc([r.ticker, r.cusip].filter(Boolean).join(" · "))}</span></td>
            <td class="num">${r.holders}</td>
            ${cells}
          </tr>`;
        })
        .join("");
    };
    sel.onchange = draw;
    draw();
  }


  /* ------------------------------------------------------------ 主题切换 */
  function paintThemeSwitch() {
    const pref = currentPref();
    $$("#theme-switch button").forEach((b) => {
      const on = b.dataset.themeSet === pref;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
  }

  function applyTheme(pref) {
    const theme = resolveTheme(pref);
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.setAttribute("data-theme-pref", pref);
    readTheme();          // 重新读 CSS 变量
    paintThemeSwitch();
    redrawAll();          // 图表用新配色重绘
  }

  function setThemePref(pref) {
    try {
      localStorage.setItem("igt-theme", pref);
    } catch (e) {}
    applyTheme(pref);
  }

  // 切换主题时重绘所有图表（数据不变，只换颜色）
  function redrawAll() {
    if (typeof renderInst === "function" && INST.length) renderInst();
    if (typeof renderPol === "function" && POL.length) renderPol();
    if (typeof renderCross === "function" && CROSS.length) renderCross();
    if (typeof renderBriefs === "function") renderBriefs();
  }

  $$("#theme-switch button").forEach((b) =>
    b.addEventListener("click", () => setThemePref(b.dataset.themeSet))
  );

  // 跟随系统时，系统主题变化要即时反映
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const onSystemChange = () => {
    if (currentPref() === "auto") applyTheme("auto");
  };
  if (mq.addEventListener) mq.addEventListener("change", onSystemChange);
  else if (mq.addListener) mq.addListener(onSystemChange);

  paintThemeSwitch();

  /* ================================================================== init */
  renderInstPick();
  renderInst();
  if (POL.length) {
    renderPolPick();
    renderPol();
  }
  renderCross();
  renderBriefs();
})();
