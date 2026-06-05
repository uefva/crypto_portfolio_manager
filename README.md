# crypto_portfolio_manager

命令行加密货币持仓管理工具。

## 运行

命令行界面：

```bash
python crypto_portfolio_manager.py
```

图形界面：

```bash
python crypto_portfolio_gui.py
```

## 文件结构

```text
crypto_portfolio_manager.py      # 程序启动入口
crypto_portfolio_gui.py          # 图形界面启动入口
crypto_portfolio/                # 应用代码
  __init__.py
  cli.py                         # 命令行菜单和用户输入
  gui.py                         # 图形化增删改查界面
  portfolio_manager.py           # 持仓、交易、备份、价格查询逻辑
requirements.txt                 # Python 依赖
portfolio.json                   # 本地持仓数据，不提交到仓库
portfolio_backups/               # 自动备份目录，不提交到仓库
holding_snapshots/               # 持仓查询结果快照，不提交到仓库
okx_credentials.json             # 本地凭据，不提交到仓库
```
