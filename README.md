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
server_config.ini                # 服务端配置，包含币种、采集间隔和日志等级
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

服务端排错时可以把 `server_config.ini` 的日志等级调成：

```ini
[logging]
level = DEBUG
```

`DEBUG` 会打印每次接口返回给客户端的数据，排错完成后建议改回 `INFO`。

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

`/api/prices/*` 为旧版加密货币接口，`/api/assets/*` 为新版股票、基金、加密货币混合资产接口。
