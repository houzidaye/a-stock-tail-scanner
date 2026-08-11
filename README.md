# A股尾盘扫描 · 9条规则全满足

基于杨永兴「尾盘八法/隔夜套利法」+ 20日实体涨停确认的 A 股尾盘选股工具。

## 9 条规则（全部满足）

| # | 规则 | 阈值 |
|---|------|------|
| ① | 涨幅 | 3%–5% |
| ② | 量比 | > 1 |
| ③ | 换手率 | 5%–10% |
| ④ | 流通市值 | 50–200 亿 |
| ⑤ | 成交量温和放大 | 近5日均量的 1.2–2 倍 |
| ⑥ | 均线多头 | MA5 > MA10 > MA20 且价格站上MA5 |
| ⑦ | 分时强度 | 全天分时线在均价线上方 |
| ⑧ | 尾盘形态 | 14:45 后创当日新高且不破均价 |
| ⑨ | 涨停历史 | 近20个交易日至少1次实体涨停 |

**实体涨停** = 命中涨停价 且 当日开盘/最低价 < 涨停价（排除一字板、T字板）。板块涨停幅度：主板 10%、创业板/科创板 20%、北交所 30%。

## 架构

```
GitHub Actions (工作日 14:45 & 14:48 北京时间)
  └── run.py 调用 scanner.py 全市场扫描
       └── docs/results/YYYY-MM-DD-HHMM.json（保留3个月）
GitHub Pages (docs/)
  └── index.html 前端拉取 index.json + 各日 JSON 展示
```

## 部署到 GitHub

1. 新建仓库 `a-stock-tail-scanner`（Public 才能免费用 GitHub Pages）
2. `git push` 后进入 Settings → Pages：
   - Source: Deploy from a branch
   - Branch: `main`, folder: `/docs`
3. Settings → Actions → General → Workflow permissions 选 **Read and write**（否则 workflow 无法回推提交）
4. Workflow 会在下一个交易日 14:45 & 14:48 自动跑；也可在 Actions 标签手动 Run workflow

访问 URL: `https://<用户名>.github.io/a-stock-tail-scanner/`

## 本地手动跑一次

```bash
pip install -r requirements.txt
python3 run.py
# 结果写入 docs/results/YYYY-MM-DD-HHMM.json
```

## 文件

- `scanner.py` — 9条规则实现 + 扫描主流程
- `run.py` — GH Actions 入口：跑扫描 + 写日期 JSON + 清理3月外历史 + 重建 index.json
- `docs/index.html` — GitHub Pages 前端
- `docs/results/` — 每次扫描一份 JSON（自动生成）
- `.github/workflows/scan.yml` — GH Actions 定时任务

## 数据源

- AKShare `stock_zh_a_spot_em` — 全市场实时快照（规则1-4字段）
- AKShare `stock_zh_a_hist` — 日K线（规则5、6、9）
- AKShare `stock_zh_a_hist_min_em` — 分时数据（规则7、8）

注：东财接口从香港本地访问不稳定，故选择 GH Actions 部署。
