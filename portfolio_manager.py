import json
import requests
from datetime import datetime
from pathlib import Path
from tabulate import tabulate

DATA_FILE = "portfolio.json"

COIN_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "ADA": "cardano",
    "SOL": "solana",
    "SUI": "sui",
    "PEPE": "pepe",
    "DOGE": "dogecoin",
}


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

    def now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def buy(self, symbol, amount, price):
        symbol = symbol.upper()
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
            "date": self.now(),
            "amount": amount,
            "price": price,
            "total": total
        })

        self.save_data()
        print("买入记录已保存。")

    def sell(self, symbol, amount, price):
        symbol = symbol.upper()

        if symbol not in self.data:
            print("没有该币种持仓。")
            return

        asset = self.data[symbol]

        if amount > asset["quantity"]:
            print("卖出数量超过持仓。")
            return

        avg_cost = asset["total_cost"] / asset["quantity"] if asset["quantity"] > 0 else 0
        cost_reduction = avg_cost * amount
        total = amount * price

        asset["quantity"] -= amount
        asset["total_cost"] -= cost_reduction

        asset["transactions"].append({
            "type": "sell",
            "date": self.now(),
            "amount": amount,
            "price": price,
            "total": total
        })

        if abs(asset["quantity"]) < 1e-12:
            asset["quantity"] = 0.0
            asset["total_cost"] = 0.0

        self.save_data()
        print("卖出记录已保存。")

    def get_prices(self):
        if not self.data:
            return {}

        ids = []
        reverse_map = {}
        for symbol in self.data:
            coin_id = COIN_MAP.get(symbol)
            if coin_id:
                ids.append(coin_id)
                reverse_map[coin_id] = symbol

        if not ids:
            return {}

        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": ",".join(ids),
            "vs_currencies": "usd"
        }

        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as e:
            print(f"获取价格失败: {e}")
            return {}

        prices = {}
        for coin_id, symbol in reverse_map.items():
            prices[symbol] = raw.get(coin_id, {}).get("usd", 0.0)

        return prices

    def show_holdings(self):
        if not self.data:
            print("暂无持仓。")
            return

        print("正在查询当前最新价格......")
        prices = self.get_prices()

        rows = []
        total_value = 0.0
        total_profit = 0.0

        for symbol, asset in self.data.items():
            quantity = asset["quantity"]
            total_cost = asset["total_cost"]
            avg_cost = total_cost / quantity if quantity > 0 else 0.0
            current_price = prices.get(symbol, 0.0)
            value = quantity * current_price
            profit = value - total_cost
            profit_rate = (profit / total_cost * 100) if total_cost > 0 else 0.0

            total_value += value
            total_profit += profit

            rows.append([
                symbol,
                f"{quantity:.8f}".rstrip("0").rstrip("."),
                f"{avg_cost:.4f}",
                f"{current_price:.4f}",
                f"{value:.2f}",
                f"{profit:.2f}",
                f"{profit_rate:.2f}%"
            ])

        print(tabulate(
            rows,
            headers=["币种", "数量", "成本价", "当前价", "持仓价值", "总收益", "收益率"],
            tablefmt="grid"
        ))

        total_cost = total_value - total_profit
        total_profit_rate = (total_profit / total_cost * 100) if total_cost > 0 else 0.0

        print(f"\n总持仓价值: ${total_value:.2f}")
        print(f"总收益: {total_profit:.2f} ({total_profit_rate:.2f}%)")

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

        for symbol, asset in self.data.items():
            value = asset["quantity"] * prices.get(symbol, 0.0)
            values[symbol] = value
            total_value += value

        if total_value <= 0:
            print("总资产为 0。")
            return

        print("\n资产分布:")
        for symbol, value in sorted(values.items(), key=lambda x: x[1], reverse=True):
            pct = value / total_value * 100
            print(f"{symbol}: {value:.2f} ({pct:.1f}%)")

        print(f"总价值: ${total_value:.2f}")
