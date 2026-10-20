# 投资大佬持仓追踪

一个本地运行、数据全部来自**原始政府披露文件**的持仓追踪网站。

打开 `site/index.html` 即可使用（无需服务器、无需联网）。

---

## 追踪对象

| 人物 | 申报主体 | 披露制度 | 说明 |
|---|---|---|---|
| 段永平 | H&H International Investment, LLC（CIK 1759760） | SEC 13F-HR | 段永平在 SEC 备案的投资主体 |
| 沃伦·巴菲特 | Berkshire Hathaway Inc（CIK 1067983） | SEC 13F-HR | 伯克希尔整体申报 |
| 凯瑟琳·伍德（木头姐） | ARK Investment Management LLC（CIK 1697748） | SEC 13F-HR | ARK 各主动 ETF 合计持仓 |
| 南希·佩洛西 | Hon. Nancy Pelosi（CA-11） | STOCK Act PTR + 年度报告 | 众议院书记处（House Clerk）发布 |
| 唐纳德·特朗普 | Donald J. Trump（白宫） | OGE Form 278-T + 278e | 美国政府伦理署（OGE）发布 |

> 前三者是**机构持仓**（有完整持仓快照，可算占比与调仓）；
> 后两者是**个人财务披露**（只有区间金额的交易流水，年度报告才有持仓快照）。

---

## 网页功能

**机构持仓**
- 切换 3 家机构 × 9 个季度
- Top 10 持仓分布环形图（占比 = 市值 / 全部申报市值）
- 本季调仓幅度条形图（**股数**变化率，剔除价格波动）
- 申报总市值 9 季度趋势、仓位动作汇总（新建/加仓/不变/减仓/清仓）
- 持仓明细表：持股数、市值、占比、股数变化、占比变化、动作、**每行的 SEC 原始文件链接**
- 加仓 / 减仓 Top 榜

**政客交易**
- 切换佩洛西 / 特朗普，可选最近 12 / 24 个月或全部
- 按月买卖申报金额、交易最活跃标的
- 年度报告持仓快照 Top 10（佩洛西 2024/2025/2026 年报，特朗普 2025/2026 年报）
- 逐笔交易明细，可按买卖方向筛选，**每笔直达原始 PDF 的页码**

**本期变化摘要**（`scripts/summary.py` 规则模板生成，可复现、可追溯）
- 单独一个标签页：5 个人/机构各一段，并排对比
- 每个对象页顶部也有一段，随季度/人物切换联动
- 机构：最新季 vs 上季——申报市值与环比、持仓数、加仓/减仓 Top3（股数口径）、新建/清仓、集中度变化、最大单项变动
- 政客：最新一份披露的交易内容 + 与上份对比 + 累计买卖方向 + 年度报告持仓快照（明确标注「交易流水口径，不是持仓变化」）

**共同持仓**
- 按 CUSIP 对齐三家机构的同一季度持仓，只列出至少两家同时持有的标的
- 一眼看出同一标的上谁在加、谁在减

**数据说明**
- 口径、已知限制、数据源、构建时间

---

## 每个数字的出处

| 数据 | 出处 | 页面中的呈现 |
|---|---|---|
| 机构持仓/市值/股数 | SEC EDGAR 13F-HR information table (XML) | 明细表「出处」列：form + accession + 申报日，可点开原始 XML |
| 季度 filing 元信息 | EDGAR filing index / primary_doc.xml | 季度标题旁 `filing index ↗` / `primary_doc.xml ↗` |
| 佩洛西交易 | House Clerk PTR PDF | 「出处」列直达 PDF + 页码 |
| 佩洛西持仓快照 | House Clerk 年度报告 PDF | 快照表「出处」列 + 页码 |
| 特朗普交易 | OGE Form 278-T PDF | 「出处」列直达 PDF + 页码 |
| 特朗普持仓快照 | OGE Form 278e PDF | 快照表「出处」列 + 页码 |

---

## 口径与已知限制（重要）

1. **13F 只覆盖美国上市证券的多头与期权**，不含现金、债券、海外持股、空头及部分衍生品，因此「申报市值」≠ 全部资产。
2. **伯克希尔同一 CUSIP 会出现多行**（不同子公司分别申报）。本站按 `CUSIP + put/call` 合并后再比较，否则环比会出现假信号。
3. **市值变化 = 价格 × 股数**。判断真实加减仓请看「股数变化」，市值变化里含股价涨跌。
4. **13F 有最长 45 天滞后**，季末持仓在季后 45 天内申报。
5. **13F-HR/A 通常是「部分更正」**，本站按「基础表 + 更正表逐条覆盖」合并，并在季度信息里标注修订记录。
6. **政客只披露区间金额**（如 `$1,000,001 – $5,000,000`），且没有实时持仓快照。金额一律按原文区间展示，**不做点估计**；图表中的「金额下限合计」只累加区间下限，是保守口径。
7. **OGE 的 PDF 是扫描件 OCR**，日期年份偶有误识（如 2026 → 2028）。本站以「申报日」为硬约束修正：交易日期不可能晚于申报日，超出则回退。原始区间金额仍以 PDF 原文为准。
8. **13F 表内 `value` 为美元**（2023 年后新 schema），不做千倍换算。
9. **摘要全部由规则模板生成**，不含人工措辞或模型补写；每条摘要底部都标注来源 filing 与申报日。数字若与原文不符，以原文链接为准。

---

## 目录结构

```
investment-guru-tracker/
├─ site/                     # 交付物：直接双击 index.html
│  ├─ index.html
│  ├─ styles.css
│  ├─ app.js
│  ├─ data.js                # 构建产物（window.SITE_DATA）
│  └─ vendor/echarts.min.js
├─ scripts/
│  ├─ common.py              # HTTP 会话、缓存、日志
│  ├─ fetch_13f.py           # SEC 13F-HR → data/raw/13f_raw.json
│  ├─ fetch_politicians.py   # House Clerk + OGE → data/raw/politicians_raw.json
│  ├─ house_ptr.py           # 众议院 PTR PDF 解析（坐标分栏）
│  ├─ house_annual.py        # 众议院年度报告 PDF 解析（持仓快照）
│  ├─ oge_278t.py            # OGE 278-T PDF 解析（OCR 容错）
│  ├─ oge_278e.py            # OGE 278e Part 6 解析（持仓快照）
│  ├─ download_oge.py        # OGE 大文件并发下载（可断点续传）
│  ├─ summary.py             # 规则模板生成「本期变化摘要」
│  ├─ build_data.py          # 计算指标 → site/data.js
│  ├─ update_all.py          # 一键全量更新
│  ├─ daily_check.py         # 每日变更检测（+ 有变化时重建）
│  └─ install_task.ps1       # 注册 Windows 计划任务
├─ data/
│  ├─ raw/                   # 原始 PDF + 抓取结果 JSON
│  ├─ cache/                 # 每个 filing 的解析缓存
│  ├─ state.json             # 每日检测的上次状态
│  └─ site_data.json         # 同 site/data.js 的 JSON 版
└─ logs/
   ├─ daily.log              # 抓取/构建日志
   └─ task.log               # 计划任务日志
```

---

## 使用

```powershell
# 全量更新（增量抓取 + 重建网页数据）
python scripts\update_all.py

# 强制重新下载所有 13F 表
python scripts\update_all.py --full

# 只检测是否有新披露（不抓取）
python scripts\daily_check.py --no-fetch
```

然后打开 `site/index.html`。

---

## 每日自动检测

已注册 Windows 计划任务 **`InvestmentGuruTracker-Daily`**，每天 **08:10** 运行：

1. 检查 SEC 13F 新 accession、House Clerk 新增 PTR/年报、OGE 新增 278-T
2. 与 `data/state.json` 里的上次状态比对
3. **有变化**才增量抓取并重建 `site/data.js`；无变化直接退出（不浪费请求）
4. 全程写入 `logs/daily.log` / `logs/task.log`

```powershell
# 重新注册 / 改时间
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -At "08:10"

# 立即跑一次
Start-ScheduledTask -TaskName "InvestmentGuruTracker-Daily"

# 查看状态
Get-ScheduledTaskInfo -TaskName "InvestmentGuruTracker-Daily"

# 卸载
Unregister-ScheduledTask -TaskName "InvestmentGuruTracker-Daily" -Confirm:$false
```

退出码：`0` 无变化 · `10` 有变化并已重建 · `1` 出错。

---

## 数据抓取注意事项

- **必须绕过本地 HTTP 代理**：`sec.gov` 与 `disclosures-clerk.house.gov` 在代理下会 TLS 失败。脚本内已设 `session.trust_env = False`。
- **SEC 要求 User-Agent 带联系方式**，且请求频率 ≤ 10 次/秒；脚本已限速。
- **OGE 文件服务器很慢**（单个大 PDF 60–270 秒），`download_oge.py` 用 6 线程并发且跳过已下载文件。
- 所有原始文件都保存在 `data/raw/`，可离线复查。

---

数据全部取自美国政府公开披露文件。本项目为研究用途，不构成投资建议。
