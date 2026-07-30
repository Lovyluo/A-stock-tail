# Phase 6.2: News Coverage and Stock Catalog

## Open-Source Reference Evaluation

The news aggregation design was reviewed against four public projects:

| Project | License | Strength | Main limitation for this workspace |
|---|---|---|---|
| ZhuLinsen/daily_stock_analysis | MIT | Active multi-source news, announcement, fallback, and diagnostics design | Full project is much larger and includes optional external services and LLM workflows |
| akfamily/akshare | MIT | Broad financial data coverage | A data library rather than a focused news workbench; source wrappers can change frequently |
| tkfy920/qstock | MIT | Lightweight financial-news API | Core news implementation is older and has fewer diagnostics |
| casual-silva/NewsCrawl | Not declared | Large general-purpose crawler platform | Requires Scrapy, MySQL, Redis, and deployment services; license is unclear |

Selected reference: `ZhuLinsen/daily_stock_analysis`, reviewed at commit
`aa68d45d7f9e86948a66393bb9edf443cfc92540`.

Only its high-level source-provider, fallback, source-status, and NewsNow ideas are
adapted. A-stock-tail keeps its own lightweight implementation and does not
vendor the reference repository.

## News Sources

The briefing uses independent source adapters:

- Eastmoney 7x24 global financial news
- direct CLS telegraph when available
- NewsNow CLS hot topics
- NewsNow WallstreetCN quick news
- NewsNow Jin10 global macro news
- NewsNow Xueqiu hot-stock attention
- Eastmoney stock news for current holdings and observation candidates
- CNINFO announcements for current holdings and observation candidates

Items are normalized, time-window filtered, and title-deduplicated before rule
classification. Missing sources are recorded. One failed source does not erase
content returned by healthy sources.

The public NewsNow instance is a fallback and can be changed through
`news_briefing.newsnow_base_url`. It is not treated as an official or guaranteed
service.

## Stock Code and Name Catalog

The catalog is stored at `overnight_quant/data/cache/stock_catalog.csv` and is a
runtime cache, so it is not committed.

Update priority:

1. Sina A-share market center full listing
2. Eastmoney full-market list fallback
3. Tencent quote lookup for a single missing code

Position input resolves a six-digit code to a canonical name. Existing position
names are also displayed with the catalog name when available. The maintenance
area provides a manual full-catalog refresh for newly listed or renamed stocks.

## Safety Boundary

These capabilities only organize public information and manual position
records. They do not place orders, call broker trading APIs, click brokerage
software, or bypass any session or risk gate.
