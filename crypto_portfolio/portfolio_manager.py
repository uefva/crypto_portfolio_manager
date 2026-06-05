import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from tabulate import tabulate

DATA_FILE = "portfolio.json"
BACKUP_DIR = "portfolio_backups"
MAX_BACKUPS = 100
HOLDING_SNAPSHOT_DIR = "holding_snapshots"

COIN_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "ADA": "cardano",
    "SOL": "solana",
    "SUI": "sui",
    "PEPE": "pepe",
    "DOGE": "dogecoin",
}

OKX_SYMBOL_MAP = {
    "BTC": "BTC-USDT",
    "ETH": "ETH-USDT",
    "ADA": "ADA-USDT",
    "SOL": "SOL-USDT",
    "SUI": "SUI-USDT",
    "PEPE": "PEPE-USDT",
    "DOGE": "DOGE-USDT",
}

BINANCE_SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "ADA": "ADAUSDT",
    "SOL": "SOLUSDT",
    "SUI": "SUIUSDT",
    "PEPE": "PEPEUSDT",
    "DOGE": "DOGEUSDT",
}

PRICE_TIMEOUT = 4


class PortfolioManager:
    def __init__(self):
        self.data = self.load_data()

    def load_data(self):
        path = Path(DATA_FILE)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def save_data(self):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        self.backup_data()

    def backup_data(self):
        data_path = Path(DATA_FILE)
        if not data_path.exists():
            return

        backup_dir = Path(BACKUP_DIR)
        backup_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = backup_dir / f"{data_path.stem}_{timestamp}{data_path.suffix}"
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

        backups = sorted(
            backup_dir.glob(f"{data_path.stem}_*{data_path.suffix}"),
            key=lambda path: path.name
        )
        for old_backup in backups[:-MAX_BACKUPS]:
            old_backup.unlink()

    def now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def normalize_trade_date(self, trade_date=None):
        if trade_date is None or str(trade_date).strip() == "":
            return self.now()

        trade_date = str(trade_date).strip()
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d"
        ]
        for fmt in formats:
            try:
                parsed = datetime.strptime(trade_date, fmt)
                return parsed.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass

        print("日期格式无效，请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS。")
        return None

    def validate_trade(self, symbol, amount, price):
        symbol = symbol.upper().strip()
        if not symbol:
            print("币种不能为空。")
            return None
        if amount <= 0 or price <= 0:
            print("数量和价格必须大于 0。")
            return None
        return symbol

    def get_symbols(self):
        return sorted(self.data.keys())

    def get_buy_transactions(self, symbol):
        symbol = symbol.upper().strip()
        if symbol not in self.data:
            return []

        buys = []
        for index, tx in enumerate(self.data[symbol].get("transactions", [])):
            if tx.get("type") == "buy":
                buys.append((index, tx))
        return buys

    def get_transactions(self, symbol=""):
        symbol = symbol.upper().strip()
        if symbol and symbol not in self.data:
            return []

        assets = {symbol: self.data[symbol]} if symbol else self.data
        transactions = []
        for sym, asset in assets.items():
            for index, tx in enumerate(asset.get("transactions", [])):
                transactions.append({
                    "symbol": sym,
                    "index": index,
                    "type": tx.get("type", ""),
                    "date": tx.get("date", ""),
                    "amount": tx.get("amount", 0),
                    "price": tx.get("price", 0),
                    "total": tx.get("total", 0),
                })

        return sorted(transactions, key=lambda item: item["date"])

    def update_transaction(self, symbol, transaction_index, tx_type, amount, price, trade_date):
        symbol = symbol.upper().strip()
        tx_type = tx_type.strip().lower()
        if tx_type not in {"buy", "sell"}:
            print("交易类型必须是 buy 或 sell。")
            return False

        if self.validate_trade(symbol, amount, price) is None:
            return False

        trade_date = self.normalize_trade_date(trade_date)
        if trade_date is None:
            return False

        if symbol not in self.data:
            print("未找到该币种。")
            return False

        transactions = self.data[symbol].get("transactions", [])
        if transaction_index < 0 or transaction_index >= len(transactions):
            print("未找到该交易记录。")
            return False

        new_transactions = [item.copy() for item in transactions]
        new_transactions[transaction_index] = {
            "type": tx_type,
            "date": trade_date,
            "amount": amount,
            "price": price,
            "total": amount * price
        }

        rebuilt = self.rebuild_asset(new_transactions)
        if rebuilt is None:
            print("修改失败：修改后账单会导致卖出数量超过持仓，原有账单未改变。")
            return False

        quantity, total_cost = rebuilt
        self.data[symbol]["transactions"] = new_transactions
        self.data[symbol]["quantity"] = quantity
        self.data[symbol]["total_cost"] = total_cost
        self.save_data()
        print("交易记录已更新。")
        return True

    def delete_transaction(self, symbol, transaction_index):
        symbol = symbol.upper().strip()
        if symbol not in self.data:
            print("未找到该币种。")
            return False

        transactions = self.data[symbol].get("transactions", [])
        if transaction_index < 0 or transaction_index >= len(transactions):
            print("未找到该交易记录。")
            return False

        new_transactions = [
            item for index, item in enumerate(transactions)
            if index != transaction_index
        ]
        rebuilt = self.rebuild_asset(new_transactions)
        if rebuilt is None:
            print("删除失败：删除后账单会导致卖出数量超过持仓，原有账单未改变。")
            return False

        quantity, total_cost = rebuilt
        if not new_transactions:
            del self.data[symbol]
        else:
            self.data[symbol]["transactions"] = new_transactions
            self.data[symbol]["quantity"] = quantity
            self.data[symbol]["total_cost"] = total_cost

        self.save_data()
        print("交易记录已删除。")
        return True

    def buy(self, symbol, amount, price, trade_date=None):
        symbol = self.validate_trade(symbol, amount, price)
        if symbol is None:
            return False

        trade_date = self.normalize_trade_date(trade_date)
        if trade_date is None:
            return False

        total = amount * price

        if symbol not in self.data:
            self.data[symbol] = {
                "quantity": 0.0,
                "total_cost": 0.0,
                "transactions": []
            }

        asset = self.data[symbol]
        asset["quantity"] += amount
        asset["total_cost"] += total

        asset["transactions"].append({
            "type": "buy",
            "date": trade_date,
            "amount": amount,
            "price": price,
            "total": total
        })

        self.save_data()
        print("买入记录已保存。")
        return True

    def delete_buy_order(self, symbol, transaction_index):
        symbol = symbol.upper().strip()
        if symbol not in self.data:
            print("未找到该币种。")
            return False

        transactions = self.data[symbol].get("transactions", [])
        if transaction_index < 0 or transaction_index >= len(transactions):
            print("未找到该买入订单。")
            return False

        tx = transactions[transaction_index]
        if tx.get("type") != "buy":
            print("只能删除买入订单。")
            return False

        new_transactions = [
            item for index, item in enumerate(transactions)
            if index != transaction_index
        ]
        rebuilt = self.rebuild_asset(new_transactions)
        if rebuilt is None:
            print("删除失败：删除该买入订单后，后续卖出数量会超过持仓。原有账单未改变。")
            return False

        quantity, total_cost = rebuilt
        if not new_transactions:
            del self.data[symbol]
        else:
            self.data[symbol]["transactions"] = new_transactions
            self.data[symbol]["quantity"] = quantity
            self.data[symbol]["total_cost"] = total_cost

        self.save_data()
        print("买入订单已删除。")
        return True

    def rebuild_asset(self, transactions):
        quantity = 0.0
        total_cost = 0.0

        for tx in transactions:
            amount = tx.get("amount", 0)
            price = tx.get("price", 0)
            if amount <= 0 or price <= 0:
                print("账单中存在无效数量或价格，无法重算持仓。")
                return None

            if tx.get("type") == "buy":
                quantity += amount
                total_cost += amount * price
            elif tx.get("type") == "sell":
                if amount > quantity + 1e-12:
                    return None
                avg_cost = total_cost / quantity if quantity > 0 else 0.0
                quantity -= amount
                total_cost -= avg_cost * amount
            else:
                print("账单中存在未知交易类型，无法重算持仓。")
                return None

            if abs(quantity) < 1e-12:
                quantity = 0.0
                total_cost = 0.0

        return quantity, total_cost

    def sell(self, symbol, amount, price, trade_date=None):
        symbol = self.validate_trade(symbol, amount, price)
        if symbol is None:
            return False

        trade_date = self.normalize_trade_date(trade_date)
        if trade_date is None:
            return False

        if symbol not in self.data:
            print("没有该币种持仓。")
            return False

        asset = self.data[symbol]

        if amount > asset["quantity"]:
            print("卖出数量超过持仓。")
            return False

        avg_cost = asset["total_cost"] / asset["quantity"] if asset["quantity"] > 0 else 0
        cost_reduction = avg_cost * amount
        total = amount * price

        asset["quantity"] -= amount
        asset["total_cost"] -= cost_reduction

        asset["transactions"].append({
            "type": "sell",
            "date": trade_date,
            "amount": amount,
            "price": price,
            "total": total
        })

        if abs(asset["quantity"]) < 1e-12:
            asset["quantity"] = 0.0
            asset["total_cost"] = 0.0

        self.save_data()
        print("卖出记录已保存。")
        return True

    def get_prices(self):
        if not self.data:
            return {}

        symbols = [
            symbol for symbol in self.data
            if symbol in OKX_SYMBOL_MAP or symbol in BINANCE_SYMBOL_MAP or symbol in COIN_MAP
        ]
        if not symbols:
            return {}

        prices = {}
        futures_by_symbol = {symbol: [] for symbol in symbols}
        future_meta = {}
        executor = ThreadPoolExecutor(max_workers=min(len(symbols) * 3, 16))

        try:
            for symbol in symbols:
                tasks = [
                    ("OKX", self.fetch_okx_price),
                    ("Binance", self.fetch_binance_price),
                    ("CoinGecko", self.fetch_coingecko_price),
                ]
                for source, fetch_price in tasks:
                    future = executor.submit(fetch_price, symbol)
                    futures_by_symbol[symbol].append(future)
                    future_meta[future] = (symbol, source)

            resolved_symbols = set()
            for future in as_completed(future_meta):
                symbol, source = future_meta[future]
                if symbol in resolved_symbols:
                    continue

                try:
                    price = future.result()
                except Exception as e:
                    print(f"{source} 获取 {symbol} 价格失败: {e}")
                    continue

                if price is None:
                    continue

                prices[symbol] = price
                resolved_symbols.add(symbol)

                for other_future in futures_by_symbol[symbol]:
                    if other_future is not future:
                        other_future.cancel()

                if len(resolved_symbols) == len(symbols):
                    break
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        return prices

    def fetch_okx_price(self, symbol):
        inst_id = OKX_SYMBOL_MAP.get(symbol)
        if not inst_id:
            return None

        url = "https://www.okx.com/api/v5/market/ticker"
        resp = requests.get(url, params={"instId": inst_id}, timeout=PRICE_TIMEOUT)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None
        return self.parse_price(data[0].get("last"))

    def fetch_binance_price(self, symbol):
        ticker_symbol = BINANCE_SYMBOL_MAP.get(symbol)
        if not ticker_symbol:
            return None

        url = "https://data-api.binance.vision/api/v3/ticker/price"
        resp = requests.get(url, params={"symbol": ticker_symbol}, timeout=PRICE_TIMEOUT)
        resp.raise_for_status()
        return self.parse_price(resp.json().get("price"))

    def fetch_coingecko_price(self, symbol):
        coin_id = COIN_MAP.get(symbol)
        if not coin_id:
            return None

        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": coin_id,
            "vs_currencies": "usd"
        }
        resp = requests.get(url, params=params, timeout=PRICE_TIMEOUT)
        resp.raise_for_status()
        return self.parse_price(resp.json().get(coin_id, {}).get("usd"))

    def parse_price(self, value):
        if value is None:
            return None
        price = float(value)
        if price <= 0:
            return None
        return price

    def format_quantity(self, quantity):
        return f"{quantity:.8f}".rstrip("0").rstrip(".")

    def print_holdings_table(self, rows):
        print(tabulate(
            rows,
            headers=["币种", "数量", "成本价", "当前价", "持仓价值", "总收益", "收益率"],
            tablefmt="grid",
            colalign=("left", "right", "right", "right", "right", "right", "right"),
            disable_numparse=True
        ))

    def build_holdings_snapshot(self, prices):
        rows = []
        total_value = 0.0
        total_profit = 0.0
        total_cost_for_priced_assets = 0.0
        unknown_price_symbols = []

        for symbol, asset in self.data.items():
            quantity = asset["quantity"]
            total_cost = asset["total_cost"]
            avg_cost = total_cost / quantity if quantity > 0 else 0.0
            current_price = prices.get(symbol)

            if current_price is None:
                unknown_price_symbols.append(symbol)
                rows.append([
                    symbol,
                    self.format_quantity(quantity),
                    f"{avg_cost:.4f}",
                    "价格未知",
                    "无法计算",
                    "无法计算",
                    "无法计算"
                ])
                continue

            value = quantity * current_price
            profit = value - total_cost
            profit_rate = (profit / total_cost * 100) if total_cost > 0 else 0.0

            total_value += value
            total_profit += profit
            total_cost_for_priced_assets += total_cost

            rows.append([
                symbol,
                self.format_quantity(quantity),
                f"{avg_cost:.4f}",
                f"{current_price:.4f}",
                f"{value:.2f}",
                f"{profit:.2f}",
                f"{profit_rate:.2f}%"
            ])

        total_profit_rate = (
            total_profit / total_cost_for_priced_assets * 100
            if total_cost_for_priced_assets > 0 else 0.0
        )

        return {
            "saved_at": self.now(),
            "rows": rows,
            "total_value": total_value,
            "total_profit": total_profit,
            "total_profit_rate": total_profit_rate,
            "unknown_price_symbols": unknown_price_symbols
        }

    def save_holdings_snapshot(self, snapshot):
        snapshot_dir = Path(HOLDING_SNAPSHOT_DIR)
        snapshot_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        snapshot_path = snapshot_dir / f"holdings_{timestamp}.json"
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

        return snapshot_path

    def print_holdings_snapshot(self, snapshot):
        rows = snapshot["rows"]
        unknown_price_symbols = snapshot.get("unknown_price_symbols", [])

        self.print_holdings_table(rows)

        total_label = "可计算总持仓价值" if unknown_price_symbols else "总持仓价值"
        profit_label = "可计算总收益" if unknown_price_symbols else "总收益"
        print(f"\n{total_label}: ${snapshot['total_value']:.2f}")
        print(
            f"{profit_label}: {snapshot['total_profit']:.2f} "
            f"({snapshot['total_profit_rate']:.2f}%)"
        )
        if unknown_price_symbols:
            print(f"价格未知，未计入汇总: {', '.join(unknown_price_symbols)}")

    def list_holdings_snapshots(self):
        snapshot_dir = Path(HOLDING_SNAPSHOT_DIR)
        if not snapshot_dir.exists():
            return []

        snapshots = []
        for path in sorted(snapshot_dir.glob("holdings_*.json"), reverse=True):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    snapshot = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            snapshots.append((path, snapshot))
        return snapshots

    def show_saved_holdings_snapshot(self, snapshot_path):
        with open(snapshot_path, "r", encoding="utf-8") as f:
            snapshot = json.load(f)

        print(f"\n查询时间: {snapshot.get('saved_at', '未知')}")
        self.print_holdings_snapshot(snapshot)

    def show_holdings(self):
        if not self.data:
            print("暂无持仓。")
            return

        print("正在查询当前最新价格......")
        prices = self.get_prices()
        snapshot = self.build_holdings_snapshot(prices)
        self.print_holdings_snapshot(snapshot)
        snapshot_path = self.save_holdings_snapshot(snapshot)
        print(f"本次查询结果已保存: {snapshot_path}")

    def show_history(self, symbol=""):
        symbol = symbol.upper().strip()

        rows = []

        if symbol:
            if symbol not in self.data:
                print("未找到该币种。")
                return
            assets = {symbol: self.data[symbol]}
        else:
            assets = self.data

        for sym, asset in assets.items():
            for tx in asset["transactions"]:
                rows.append([
                    sym,
                    tx["date"],
                    "买入" if tx["type"] == "buy" else "卖出",
                    tx["amount"] if tx["type"] == "buy" else -tx["amount"],
                    tx["price"],
                    tx["total"]
                ])

        if not rows:
            print("暂无交易记录。")
            return

        rows.sort(key=lambda x: x[1])

        print(tabulate(
            rows,
            headers=["币种", "日期", "类型", "数量", "价格", "总金额"],
            tablefmt="grid",
            floatfmt=".6f"
        ))

    def show_distribution(self):
        if not self.data:
            print("暂无持仓。")
            return

        print("正在查询当前最新价格......")
        prices = self.get_prices()

        values = {}
        total_value = 0.0
        unknown_price_symbols = []

        for symbol, asset in self.data.items():
            current_price = prices.get(symbol)
            if current_price is None:
                unknown_price_symbols.append(symbol)
                continue
            value = asset["quantity"] * current_price
            values[symbol] = value
            total_value += value

        if total_value <= 0:
            print("可计算总资产为 0，无法展示资产分布。")
            if unknown_price_symbols:
                print(f"价格未知，未计入资产分布: {', '.join(unknown_price_symbols)}")
            return

        print("\n资产分布:")
        for symbol, value in sorted(values.items(), key=lambda x: x[1], reverse=True):
            pct = value / total_value * 100
            print(f"{symbol}: {value:.2f} ({pct:.1f}%)")

        total_label = "可计算总价值" if unknown_price_symbols else "总价值"
        print(f"{total_label}: ${total_value:.2f}")
        if unknown_price_symbols:
            print(f"价格未知，未计入资产分布: {', '.join(unknown_price_symbols)}")
