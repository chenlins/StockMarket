# GiveMeMoney 选股工具

基于 Python + AkShare 的 A 股选股工具，支持多策略筛选、回测验证和 Web 展示。

> **免责声明**：本工具仅供学习研究，不构成任何投资建议。

## 功能

- **数据层**：AkShare 获取 A 股行情与财务数据，SQLite 本地缓存
- **股票池**：默认仅分析沪深主板普通股，涨幅 0–10%
- **指标层**：MA、RSI、MACD、量比、PE/PB/ROE 打分
- **策略层**：价值投资 + 动量趋势，多策略加权排名
- **回测**：均线金叉/死叉简单回测
- **调度**：交易日收盘后自动选股
- **展示**：FastAPI Web 页面 + CSV 导出

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 运行选股

```bash
python main.py screen
```

导出 CSV：

```bash
python main.py screen --export
```

### 3. 单股回测

```bash
python main.py backtest 600519
```

### 4. 启动 Web 服务（形态选股界面）

```bash
python main.py serve
```

浏览器访问 http://127.0.0.1:8000 ，支持：
- 量价齐升、缩量回调、放量突破等 7 种形态策略
- 左侧筛选面板 + 结果表格 + K 线缩略图
- 形态说明、收藏/排除、推荐候选股

### 5. 定时选股（交易日 16:30）

```bash
python main.py schedule
```

## 项目结构

```
GiveMeMoney/
├── config.yaml          # 策略与系统配置
├── main.py              # CLI 入口
├── data/                # 数据获取与缓存
│   ├── fetcher.py
│   └── storage.py
├── indicators/          # 技术指标 & 财务指标
│   ├── technical.py
│   └── fundamental.py
├── strategies/          # 选股策略
│   ├── base.py
│   ├── value.py
│   └── momentum.py
├── backtest/            # 回测引擎
│   └── engine.py
├── scheduler/           # 定时任务
│   └── jobs.py
├── api/                 # Web API
│   └── app.py
└── web/                 # Web 界面 (HTML/CSS/JS)
    ├── index.html
    ├── styles.css
    └── app.js
```

## 配置说明

编辑 `config.yaml` 调整策略参数：

| 配置项 | 说明 |
|--------|------|
| `strategies.value.pe_max` | PE 上限 |
| `strategies.value.roe_min` | ROE 下限 (%) |
| `strategies.momentum.ma_short/ma_long` | 短/长均线周期 |
| `screening.main_board_only` | 仅沪深主板 |
| `screening.ordinary_only` | 仅普通股，排除 ST |
| `screening.change_pct_min/max` | 涨跌幅区间 (%) |
| `screening.top_n` | 输出股票数量 |

## 策略说明

### 价值投资 (value)
- PE < 25，PB < 3
- ROE 越高得分越高
- 适合中长期基本面筛选

### 动量趋势 (momentum)
- 收盘价站上 MA5、MA20
- MA5 > MA20（多头排列）
- 成交量高于 5 日均量 1.2 倍
- 适合短线趋势跟踪

## 后续扩展

- [ ] 接入 Tushare Pro（更稳定的历史数据）
- [ ] 增加北向资金、龙虎榜等资金面策略
- [ ] 多策略组合回测与参数优化
- [ ] 微信/钉钉推送提醒
- [ ] 前端图表可视化
