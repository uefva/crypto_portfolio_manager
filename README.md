# crypto_portfolio_manager

股票、基金、加密货币统一持仓管理工具。图形界面支持多资产录入、人民币总资产收益、分类筛选和收益走势；命令行界面保留加密货币的基础操作。

## 运行

命令行界面：

```bash
python crypto_portfolio_manager.py
```

图形界面：

```bash
python crypto_portfolio_gui.py
```

价格服务端：

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
  cli.py                         # 命令行菜单和用户输入，默认处理加密货币
  gui.py                         # 图形化多资产增删改查界面和收益走势图
  market_data.py                 # 股票、基金、加密货币、汇率行情查询
  price_server.py                # SQLite 多资产价格服务端和 HTTP 接口
  portfolio_manager.py           # 持仓、交易、备份、人民币收益逻辑
requirements.txt                 # Python 依赖
portfolio.json                   # 本地持仓数据，自动升级到 v2，不提交到仓库
portfolio_backups/               # 自动备份目录，不提交到仓库
holding_snapshots/               # 持仓查询结果快照，不提交到仓库
price_history.sqlite3            # 服务端价格历史数据库，不提交到仓库
okx_credentials.json             # 本地凭据，不提交到仓库
```

## 图形界面配置

GUI 的服务端地址从 `gui_config.ini` 读取：

```ini
[server]
url = http://120.25.206.204:8765
```

如果配置文件不存在，或 `url` 为空，GUI 会回退到 `http://127.0.0.1:8765`。这个地址只影响“收益走势”页里“服务端价格记录”的读取；持仓录入、交易记录和本地历史快照不依赖服务端。

价格服务端只按照 `server_config.ini` 采集资产，不读取服务器本机的 `portfolio.json`。要让服务器采集股票或基金，请在配置文件中维护采集列表：

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

每个分类都可以用 `enabled = false` 单独关闭采集。GUI 的“服务端价格记录”仍会按本地持仓请求历史数据；只有该资产也出现在服务器配置里并被采集过，走势图才会有数据。

服务端采集价格时会先尝试获取全部配置资产。若部分资产失败，会按 `fetch_retries` 重试失败资产，每次重试之间等待 `retry_backoff_seconds` 秒。只有全部资产都成功获取时，本轮数据才会写入数据库；如果最终仍有失败资产，本轮状态为 `failed`，不会保存任何部分价格，避免客户端收益曲线使用不完整数据。

服务端排错时可以把 `server_config.ini` 的日志等级调成：

```ini
[logging]
level = DEBUG
```

`DEBUG` 会打印每次接口返回给客户端的数据，排错完成后建议改回 `INFO`。

## 人民币换算逻辑

每个资产都有一个本币：加密货币和美股默认 USD，港股默认 HKD，A 股和基金默认 CNY。录入买入或卖出时，系统会同时记录本币成交额和人民币成交额：

```text
本币成交额 = 数量 * 成交价格
人民币成交额 = 本币成交额 * 当时记录的汇率
```

汇率优先使用用户在交易表单中手动输入的值；如果留空，则自动查询对应币种到 CNY 的汇率；如果汇率接口失败，会使用内置默认估算值。当前持仓市值用“当前价格 * 当前汇率”折算成人民币，收益用“当前人民币市值 - 历史人民币成本”计算。

需要注意的风险：

- 如果录入历史交易时没有填写当时汇率，系统会使用录入时查询到的汇率，不一定等于真实交易日汇率。
- 汇率接口失败并使用默认估算汇率时，人民币成本和收益会有偏差。
- 老数据迁移缺少历史汇率，只能用迁移时的 USD/CNY 近似补齐。
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

`/api/assets/history` 的 `limit` 表示最多返回多少个时间点，而不是最多返回多少条数据库记录。由于每个时间点可能包含多个资产价格，服务端会先筛选时间点，再返回这些时间点下的完整资产价格。`limit=0` 或 `limit=all` 表示不限制时间点数量，GUI 在“全部时间”查询时会使用不限量模式。

旧版本数据库中的 `price_history` 表如果存在，会在服务端启动或初始化数据库时自动迁移到新版 `asset_price_history` 表。迁移使用 `INSERT OR IGNORE`，不会覆盖新表已有的同一资产同一时间点数据。迁移后旧表不会被删除，但后续服务端不再读写旧表。
