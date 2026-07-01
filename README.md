# crypto_portfolio_manager

股票、基金、加密货币统一持仓管理工具。图形界面支持多资产录入、人民币总资产收益、分类筛选和收益走势；命令行界面保留加密货币的基础操作。

## 快速开始

推荐先启动服务端，再启动图形界面。服务端现在同时负责价格历史和投资组合数据；图形界面会优先连接服务端，连接失败时回退读取本地 `portfolio.json`。

1. 安装依赖：

```bash
pip install -r requirements.txt
```

2. 启动服务端：

```bash
python crypto_price_server.py
```

3. 启动图形界面：

```bash
python crypto_portfolio_gui.py
```

4. 首次从旧版本升级时，在图形界面“资产管理”页点击“导入本地数据到服务端”，把本地 `portfolio.json` 显式导入服务端数据库。

## 运行入口

命令行界面：

```bash
python crypto_portfolio_manager.py
```

图形界面：

```bash
python crypto_portfolio_gui.py
```

服务端：

```bash
python crypto_price_server.py
```

Linux 后台重启价格服务：

```bash
chmod +x scripts/restart_server.sh
scripts/restart_server.sh
```

脚本使用的启动命令等价于：

```bash
nohup .venv/bin/python -u crypto_price_server.py > server.log 2>&1 &
```

也可以直接双击：

```text
start_gui.bat
start_server.bat
```

## 文件结构

```text
crypto_portfolio_manager.py      # 程序启动入口
crypto_portfolio_gui.py          # 图形界面启动入口
crypto_price_server.py           # 价格采集服务端入口
start_gui.bat                    # 双击启动图形界面
start_cli.bat                    # 双击启动命令行界面
start_server.bat                 # 双击启动价格服务端
server_config.ini                # 服务端配置，包含采集资产、采集间隔和日志等级
gui_config.ini                   # 图形界面配置，包含客户端连接的服务端地址
crypto_portfolio/                # 应用代码
  __init__.py
  cli/                           # 命令行菜单和用户输入
  desktop/                       # 图形界面主窗口、配置和后续 tab 模块
  domain/                        # 资产、市场、币种等通用领域定义
  market/                        # 股票、基金、加密货币、汇率行情查询
  portfolio/                     # 本地组合管理、服务端 API client、导入导出
  server/                        # SQLite 价格采集、投资组合服务端和 HTTP 接口
  gui.py                         # 兼容导出：旧 GUI 导入路径
  market_data.py                 # 兼容导出：旧行情导入路径
  price_server.py                # 兼容导出：旧服务端导入路径
  portfolio_api_client.py        # 兼容导出：旧 API client 导入路径
  portfolio_manager.py           # 兼容导出：旧本地组合管理导入路径
requirements.txt                 # Python 依赖
portfolio.json                   # 本地持仓数据，自动升级到 v2，不提交到仓库
portfolio_backups/               # 自动备份目录，不提交到仓库
holding_snapshots/               # 持仓查询结果快照，不提交到仓库
price_history.sqlite3            # 服务端价格历史数据库，不提交到仓库
okx_credentials.json             # 本地凭据，不提交到仓库
```

兼容导出文件会保留旧导入路径，例如 `from crypto_portfolio.price_server import main` 仍然可用；新代码建议直接使用 `crypto_portfolio.server`、`crypto_portfolio.market`、`crypto_portfolio.portfolio` 和 `crypto_portfolio.desktop` 下的模块。

## 图形界面配置与迁移

GUI 的服务端地址从 `gui_config.ini` 读取：

```ini
[server]
url = http://120.25.206.204:8765
```

如果配置文件不存在，或 `url` 为空，GUI 会回退到 `http://127.0.0.1:8765`。

当前 GUI 的主数据源优先级：

1. 服务端投资组合 API：资产目录、交易记录、持仓、收益汇总、服务端收益曲线都优先从这里读取。
2. 本地 `portfolio.json`：服务端不可用时作为只读回退，避免客户端无法打开。
3. 本地 `holding_snapshots/`：仍用于保存手动查询后的持仓快照。

旧版本用户迁移步骤：

1. 确认 `portfolio.json` 仍在项目根目录。
2. 启动服务端，确保 `server_config.ini` 的 `[prices] database` 指向要使用的 SQLite 文件。
3. 启动 GUI，并确认 `gui_config.ini` 指向该服务端。
4. 打开“资产管理”页，点击“导入本地数据到服务端”。
5. 导入会保留原 `portfolio.json`，并在 `portfolio_backups/` 里生成备份；重复导入时，同资产同日期同类型同数量同价格的交易会自动跳过。

导入后，新增/修改/删除资产和交易都会优先写入服务端数据库。

## 服务端配置

价格采集任务只按照 `server_config.ini` 采集资产，不会从服务器本机的 `portfolio.json` 自动生成采集列表。要让服务器采集股票或基金，请在配置文件中维护采集列表：

```ini
[prices]
interval_minutes = 30
database = price_history.sqlite3
fetch_retries = 3
retry_backoff_seconds = 2

[crypto]
enabled = true
symbols = BTC,ETH,ADA,SOL,SUI,PEPE,DOGE

[fund]
enabled = true
codes = 270042,017437,009478

[stock]
enabled = true
us = QQQM,BABA
hk = 00700
sh = 600519
sz = 000001
```

每个分类都可以用 `enabled = false` 单独关闭采集。投资组合中的资产不等于价格采集列表：资产和交易保存在服务端投资组合表里；只有资产也出现在 `server_config.ini` 的采集列表并被采集过，服务端收益曲线才会有历史价格点。

服务端采集价格时会先尝试获取全部配置资产。若部分资产失败，会按 `fetch_retries` 重试失败资产，每次重试之间等待 `retry_backoff_seconds` 秒。只有全部资产都成功获取时，本轮数据才会写入数据库；如果最终仍有失败资产，本轮状态为 `failed`，不会保存任何部分价格，避免客户端收益曲线使用不完整数据。

服务端排错时可以把 `server_config.ini` 的日志等级调成：

```ini
[logging]
level = DEBUG
```

`DEBUG` 会打印每次接口返回给客户端的数据，排错完成后建议改回 `INFO`。

## 人民币换算逻辑

每个资产都有一个本币：加密货币和美股默认 USD，港股默认 HKD，A 股和基金默认 CNY。录入买入或卖出时，订单只记录本币口径：

```text
本币成交额 = 数量 * 成交价格
```

订单不再手动输入汇率，也不使用下单时汇率计算人民币成本。持仓和收益在查询时折算：

```text
当前人民币市值 = 持仓数量 * 当前价格 * 当前汇率
当前人民币成本 = 剩余本币成本 * 当前汇率
当前收益 = 当前人民币市值 - 当前人民币成本
```

服务端收益曲线使用历史价格点里保存的 `fx_to_cny`：

```text
历史人民币市值 = 持仓数量 * 历史 price_cny
历史人民币成本 = 剩余本币成本 * 历史 fx_to_cny
历史收益 = 历史人民币市值 - 历史人民币成本
```

需要注意的风险：

- 该口径会让非人民币资产的人民币成本随查询时汇率变化，这是当前项目的设计选择。
- 服务端收益曲线依赖历史价格采集时保存的汇率；如果某个时间点缺少汇率，该资产不会参与该点计算。
- 如果行情接口返回的币种异常，例如 USD 价格被当成 CNY，可能导致重复换算或少换算。
- 服务端现在要求一轮全部资产价格采集成功才写库，用来避免部分资产缺失导致客户端收益曲线不准。

## 价格服务端接口

默认地址为 `http://127.0.0.1:8765`。

```text
GET  /api/health
GET  /api/symbols
GET  /api/prices/latest?symbols=BTC,ETH
GET  /api/prices/history?symbols=BTC,ETH&limit=5000
GET  /api/assets/latest?categories=基金,股票
GET  /api/assets/history?asset_ids=stock:US:QQQM&limit=5000
POST /api/refresh
```

`/api/prices/*` 为旧版加密货币接口，目前保留兼容，但底层读取新版 `asset_price_history` 表；`/api/assets/*` 为新版股票、基金、加密货币混合资产接口。

`/api/assets/history` 的 `limit` 表示最多返回多少个时间点，而不是最多返回多少条数据库记录。由于每个时间点可能包含多个资产价格，服务端会先筛选时间点，再返回这些时间点下的资产价格。`limit=0` 或 `limit=all` 表示不限制时间点数量，GUI 在“全部时间”查询时会使用不限量模式。

`/api/assets/history` 默认返回精简结构，只包含收益走势图需要的 `timestamp` 和 `price_cny`，以减少服务端组装和网络传输时间：

```json
{
  "points": [
    {
      "timestamp": "2026-01-01 00:00:00",
      "price_cny": {
        "crypto:CRYPTO:BTC": 700000.0
      }
    }
  ]
}
```

如果需要兼容旧版完整结构，可以添加 `full=1`，返回 `assets`、`prices`、`price_cny`、`fx_to_cny` 和 `sources`。服务端还支持 gzip 压缩：客户端请求头包含 `Accept-Encoding: gzip` 且响应较大时，会返回 `Content-Encoding: gzip`；常见 HTTP 客户端会自动解压。

旧版本数据库中的 `price_history` 表如果存在，会在服务端启动或初始化数据库时自动迁移到新版 `asset_price_history` 表。迁移使用 `INSERT OR IGNORE`，不会覆盖新表已有的同一资产同一时间点数据。迁移后旧表不会被删除，但后续服务端不再读写旧表。

## 投资组合服务端接口

服务端现在也可以保存资产目录和交易记录。桌面客户端会优先连接 `gui_config.ini` 中配置的服务端；如果服务端不可用，会回退读取本地 `portfolio.json` 作为只读数据。首次迁移时，可以在客户端“资产管理”页点击“导入本地数据到服务端”。

新增接口统一返回：

```json
{
  "data": {}
}
```

失败时返回：

```json
{
  "error": {
    "code": "invalid_request",
    "message": "错误说明"
  }
}
```

接口列表：

```text
GET    /api/portfolio/assets
POST   /api/portfolio/assets
PUT    /api/portfolio/assets/{asset_id}
DELETE /api/portfolio/assets/{asset_id}

GET    /api/portfolio/transactions
POST   /api/portfolio/transactions
PUT    /api/portfolio/transactions/{id}
DELETE /api/portfolio/transactions/{id}

GET    /api/portfolio/holdings?category=全部
GET    /api/portfolio/summary?category=全部
GET    /api/portfolio/profit-history?metric=收益金额
POST   /api/portfolio/import
GET    /api/portfolio/export
```

投资组合数据和价格历史共用 `server_config.ini` 中 `[prices] database` 指向的 SQLite 文件。新增表包括 `portfolio_assets`、`portfolio_transactions` 和 `portfolio_meta`。订单只保存原币成交金额；持仓和收益在查询时按实时或历史汇率折算。

### API 示例

新增资产：

```bash
curl -X POST http://127.0.0.1:8765/api/portfolio/assets \
  -H "Content-Type: application/json" \
  -d '{"category":"股票","market":"US","symbol":"QQQM","name":"纳指ETF"}'
```

新增买入交易：

```bash
curl -X POST http://127.0.0.1:8765/api/portfolio/transactions \
  -H "Content-Type: application/json" \
  -d '{"category":"股票","market":"US","symbol":"QQQM","name":"纳指ETF","type":"buy","amount":2,"price":10,"date":"2026-01-01"}'
```

新增卖出交易：

```bash
curl -X POST http://127.0.0.1:8765/api/portfolio/transactions \
  -H "Content-Type: application/json" \
  -d '{"asset_id":"stock:US:QQQM","type":"sell","amount":1,"price":12,"date":"2026-01-02"}'
```

查看持仓和汇总：

```bash
curl "http://127.0.0.1:8765/api/portfolio/holdings?category=全部"
curl "http://127.0.0.1:8765/api/portfolio/summary?category=全部"
```

导出服务端投资组合数据：

```bash
curl "http://127.0.0.1:8765/api/portfolio/export"
```

导入本地 `portfolio.json` 时，GUI 会自动读取文件并调用 `/api/portfolio/import`。如果手动调用该接口，请传入：

```json
{
  "portfolio": {
    "version": 2,
    "assets": {}
  }
}
```

## 多端扩展路线

当前迁移后的边界：

- 服务端是投资组合和历史价格的主数据源。
- 桌面 GUI 是服务端 API 的管理客户端。
- 本地 `portfolio.json` 主要用于旧数据迁移和离线只读回退。

后续增加 Web 或 App 时，应直接消费 `/api/portfolio/*` 和 `/api/assets/*`，不要再读写 `portfolio.json`。建议顺序：

1. 先做 Web 管理端，复用资产、交易、持仓和收益 API。
2. 再做移动端或 PWA，优先覆盖查看持仓、收益曲线和快速记账。
3. 如果要开放到局域网或公网，再补 API token、CORS 白名单和 HTTPS 反代。

当前服务端默认面向个人单用户、本地或内网部署，不包含账号体系。

## 常见问题

### 客户端显示“服务端不可用，已使用本地只读数据”

检查：

1. 服务端是否已启动。
2. `gui_config.ini` 的 `url` 是否正确。
3. 服务端端口是否被防火墙或安全组阻挡。
4. 浏览器或命令行访问 `http://127.0.0.1:8765/api/health` 是否返回 JSON。

### 服务端收益曲线没有数据

常见原因：

- 投资组合里已有资产，但 `server_config.ini` 没有配置这些资产的价格采集。
- 服务端刚启动，还没有完成第一轮价格采集。
- 某轮采集中有资产失败，服务端不会保存部分价格点。

### 导入后资产有了，但收益曲线仍为空

导入只迁移资产和交易，不会补历史价格。要画服务端收益曲线，需要把相关资产加入 `server_config.ini` 的采集列表，并等待服务端采集价格。
