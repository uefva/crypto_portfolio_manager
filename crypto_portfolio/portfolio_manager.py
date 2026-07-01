import json
from datetime import datetime
from pathlib import Path

from crypto_portfolio.market_data import (
    CATEGORY_ALL,
    CATEGORY_CRYPTO,
    CATEGORY_FUND,
    CATEGORY_STOCK,
    CATEGORIES,
    COIN_MAP,
    MARKET_CRYPTO,
    MARKET_FUND,
    MARKET_LABELS,
    asset_id_for,
    asset_label,
    currency_for,
    fetch_asset_quote,
    fetch_crypto_quote,
    fetch_fx_to_cny,
    fetch_quotes_for_assets,
    normalize_category,
    normalize_market,
    normalize_symbol,
    search_asset_suggestions,
    suggestion_label,
)


DATA_FILE = "portfolio.json"
BACKUP_DIR = "portfolio_backups"
MAX_BACKUPS = 100
HOLDING_SNAPSHOT_DIR = "holding_snapshots"
PORTFOLIO_VERSION = 2


def safe_float(value, default=0.0):
    try:
        if value in (None, "", "-", "--"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class PortfolioManager:
    def __init__(self):
        self.migration_needed = False
        self.data = self.load_data()
        if self.migration_needed:
            self.save_data()

    def load_data(self):
        path = Path(DATA_FILE)
        if not path.exists():
            return {}

        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        if isinstance(raw, dict) and raw.get("version") == PORTFOLIO_VERSION:
            assets = raw.get("assets", {})
            return self.normalize_assets(assets)

        self.migration_needed = True
        return self.migrate_legacy_data(raw)

    def save_data(self):
        payload = {
            "version": PORTFOLIO_VERSION,
            "assets": self.data,
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        self.backup_data(payload)

    def backup_data(self, payload=None):
        data_path = Path(DATA_FILE)
        if not data_path.exists():
            return

        backup_dir = Path(BACKUP_DIR)
        backup_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = backup_dir / f"{data_path.stem}_{timestamp}{data_path.suffix}"
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(payload or {"version": PORTFOLIO_VERSION, "assets": self.data}, f, ensure_ascii=False, indent=2)

        backups = sorted(
            backup_dir.glob(f"{data_path.stem}_*{data_path.suffix}"),
            key=lambda path: path.name,
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
            "%Y-%m-%d",
        ]
        for fmt in formats:
            try:
                parsed = datetime.strptime(trade_date, fmt)
                return parsed.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass

        print("日期格式无效，请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS。")
        return None

    def migrate_legacy_data(self, raw):
        if not isinstance(raw, dict):
            return {}

        fx_to_cny, fx_source, _estimated = fetch_fx_to_cny("USD")
        assets = {}
        for symbol, old_asset in raw.items():
            if not isinstance(old_asset, dict):
                continue

            symbol = normalize_symbol(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO)
            asset_id = asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, symbol)
            transactions = []
            for tx in old_asset.get("transactions", []):
                amount = safe_float(tx.get("amount"))
                price = safe_float(tx.get("price"))
                total = safe_float(tx.get("total"), amount * price) or amount * price
                transactions.append({
                    "type": str(tx.get("type", "")).strip().lower(),
                    "date": tx.get("date", self.now()),
                    "amount": amount,
                    "price": price,
                    "total": total,
                    "currency": "USD",
                    "fx_to_cny": fx_to_cny,
                    "fx_source": fx_source,
                    "fx_estimated": True,
                    "migrated_fx": True,
                    "total_cny": total * fx_to_cny,
                })

            asset = {
                "asset_id": asset_id,
                "category": CATEGORY_CRYPTO,
                "market": MARKET_CRYPTO,
                "symbol": symbol,
                "name": symbol,
                "currency": "USD",
                "quantity": safe_float(old_asset.get("quantity")),
                "total_cost": safe_float(old_asset.get("total_cost")),
                "total_cost_cny": safe_float(old_asset.get("total_cost")) * fx_to_cny,
                "transactions": transactions,
            }
            rebuilt = self.rebuild_asset(asset["transactions"])
            if rebuilt is not None:
                asset["quantity"], asset["total_cost"], asset["total_cost_cny"] = rebuilt
            assets[asset_id] = asset

        return assets

    def normalize_assets(self, assets):
        normalized = {}
        if not isinstance(assets, dict):
            return normalized

        for key, asset in assets.items():
            if not isinstance(asset, dict):
                continue

            category = normalize_category(asset.get("category", CATEGORY_CRYPTO))
            market = normalize_market(asset.get("market"), category)
            symbol = normalize_symbol(asset.get("symbol") or key, category, market)
            asset_id = asset.get("asset_id") or asset_id_for(category, market, symbol)
            currency = str(asset.get("currency") or currency_for(category, market)).upper()

            transactions = [
                self.normalize_transaction(tx, currency)
                for tx in asset.get("transactions", [])
                if isinstance(tx, dict)
            ]
            normalized_asset = {
                "asset_id": asset_id,
                "category": category,
                "market": market,
                "symbol": symbol,
                "name": asset.get("name") or symbol,
                "currency": currency,
                "quantity": safe_float(asset.get("quantity")),
                "total_cost": safe_float(asset.get("total_cost")),
                "total_cost_cny": safe_float(asset.get("total_cost_cny")),
                "transactions": transactions,
            }

            rebuilt = self.rebuild_asset(transactions)
            if rebuilt is not None:
                normalized_asset["quantity"], normalized_asset["total_cost"], normalized_asset["total_cost_cny"] = rebuilt
            normalized[asset_id] = normalized_asset

        return normalized

    def normalize_transaction(self, tx, currency):
        amount = safe_float(tx.get("amount"))
        price = safe_float(tx.get("price"))
        total = safe_float(tx.get("total"), amount * price) or amount * price
        tx_currency = str(tx.get("currency") or currency).upper()
        fx_to_cny = safe_float(tx.get("fx_to_cny"), 1.0)
        if fx_to_cny <= 0:
            fx_to_cny = 1.0
        fx_source = tx.get("fx_source", "")
        fx_estimated = bool(tx.get("fx_estimated", False))
        return {
            "type": str(tx.get("type", "")).strip().lower(),
            "date": tx.get("date", self.now()),
            "amount": amount,
            "price": price,
            "total": total,
            "currency": tx_currency,
            "fx_to_cny": fx_to_cny,
            "fx_source": fx_source,
            "fx_estimated": fx_estimated,
            "migrated_fx": bool(tx.get("migrated_fx", False)),
            "total_cny": safe_float(tx.get("total_cny"), total * fx_to_cny) or total * fx_to_cny,
        }

    def validate_trade(self, symbol, amount, price):
        symbol = str(symbol or "").upper().strip()
        if not symbol:
            print("代码不能为空。")
            return None
        if amount <= 0 or price <= 0:
            print("数量和价格必须大于 0。")
            return None
        return symbol

    def resolve_fx(self, currency, fx_to_cny=None):
        return 1.0, "订单不记录汇率", False

    def build_asset(self, category, market, symbol, name=""):
        category = normalize_category(category)
        market = normalize_market(market, category)
        symbol = normalize_symbol(symbol, category, market)
        asset_id = asset_id_for(category, market, symbol)
        currency = currency_for(category, market)
        return {
            "asset_id": asset_id,
            "category": category,
            "market": market,
            "symbol": symbol,
            "name": name.strip() if isinstance(name, str) and name.strip() else symbol,
            "currency": currency,
            "quantity": 0.0,
            "total_cost": 0.0,
            "total_cost_cny": 0.0,
            "transactions": [],
        }

    def get_symbols(self, category=CATEGORY_CRYPTO):
        category = normalize_category(category)
        return sorted({
            asset["symbol"]
            for asset in self.data.values()
            if asset.get("category") == category
        })

    def get_assets(self, category=CATEGORY_ALL, active_only=False):
        category = normalize_category(category) if category != CATEGORY_ALL else CATEGORY_ALL
        assets = []
        for asset in self.data.values():
            if category != CATEGORY_ALL and asset.get("category") != category:
                continue
            if active_only and asset.get("quantity", 0) <= 0:
                continue
            assets.append(asset)
        return sorted(assets, key=lambda item: (item.get("category", ""), item.get("market", ""), item.get("symbol", "")))

    def get_active_assets(self):
        return self.get_assets(CATEGORY_ALL, active_only=True)

    def find_asset_id(self, symbol, category=CATEGORY_CRYPTO, market=None):
        category = normalize_category(category)
        market = normalize_market(market, category)
        symbol = normalize_symbol(symbol, category, market)
        exact = asset_id_for(category, market, symbol)
        if exact in self.data:
            return exact

        matches = [
            asset_id for asset_id, asset in self.data.items()
            if asset.get("symbol") == symbol and asset.get("category") == category
        ]
        if len(matches) == 1:
            return matches[0]
        return exact

    def normalize_asset_input(self, category, market, symbol, name=""):
        category = normalize_category(category)
        market = normalize_market(market, category)
        symbol = normalize_symbol(symbol, category, market)
        asset_id = asset_id_for(category, market, symbol)
        name = name.strip() if isinstance(name, str) and name.strip() else symbol
        return asset_id, category, market, symbol, name

    def upsert_asset(self, category, market, symbol, name=""):
        asset_id, category, market, symbol, name = self.normalize_asset_input(category, market, symbol, name)
        if not symbol:
            print("代码不能为空。")
            return None

        if asset_id not in self.data:
            self.data[asset_id] = self.build_asset(category, market, symbol, name)
        else:
            self.data[asset_id]["name"] = name
            self.data[asset_id]["currency"] = currency_for(category, market)

        self.save_data()
        return self.data[asset_id]

    def update_asset(self, old_asset_id, category, market, symbol, name=""):
        if old_asset_id not in self.data:
            print("未找到该资产。")
            return False

        new_asset_id, category, market, symbol, name = self.normalize_asset_input(category, market, symbol, name)
        if not symbol:
            print("代码不能为空。")
            return False

        asset = self.data[old_asset_id]
        has_transactions = bool(asset.get("transactions"))
        if has_transactions and (
            category != asset.get("category")
            or market != asset.get("market")
            or symbol != asset.get("symbol")
        ):
            print("已有交易记录的资产只能修改名称。")
            return False

        if new_asset_id != old_asset_id and new_asset_id in self.data:
            print("目标资产已存在。")
            return False

        asset.update({
            "asset_id": new_asset_id,
            "category": category,
            "market": market,
            "symbol": symbol,
            "name": name,
            "currency": currency_for(category, market),
        })
        if new_asset_id != old_asset_id:
            self.data[new_asset_id] = asset
            del self.data[old_asset_id]

        self.save_data()
        return True

    def delete_asset(self, asset_id):
        if asset_id not in self.data:
            print("未找到该资产。")
            return False
        if self.data[asset_id].get("transactions"):
            print("该资产已有交易记录，不能直接删除。")
            return False

        del self.data[asset_id]
        self.save_data()
        return True

    def local_asset_suggestions(self, query="", category=CATEGORY_ALL, market=None, limit=20):
        query = str(query or "").strip().upper()
        category = normalize_category(category) if category != CATEGORY_ALL else CATEGORY_ALL
        market = normalize_market(market, category) if category != CATEGORY_ALL and market else None
        assets = []
        for asset in self.data.values():
            if category != CATEGORY_ALL and asset.get("category") != category:
                continue
            if market and asset.get("market") != market:
                continue
            if query and query not in (
                f"{asset.get('symbol', '')} {asset.get('name', '')}".upper()
            ):
                continue
            assets.append({
                "asset_id": asset.get("asset_id"),
                "category": asset.get("category"),
                "market": asset.get("market"),
                "symbol": asset.get("symbol"),
                "name": asset.get("name") or asset.get("symbol"),
                "currency": asset.get("currency") or currency_for(asset.get("category"), asset.get("market")),
            })
        return sorted(assets, key=lambda item: suggestion_label(item))[:limit]

    def asset_suggestions(self, query="", category=CATEGORY_ALL, market=None, limit=20):
        local = self.local_asset_suggestions(query, category, market, limit=limit)
        if not query or category == CATEGORY_ALL or category == CATEGORY_CRYPTO:
            return local

        try:
            remote = search_asset_suggestions(query, category, market, limit=limit)
        except Exception:
            remote = []

        merged = []
        seen = set()
        for asset in local + remote:
            asset_id = asset.get("asset_id") or asset_id_for(
                asset.get("category"), asset.get("market"), asset.get("symbol")
            )
            if asset_id in seen:
                continue
            seen.add(asset_id)
            merged.append(asset)
            if len(merged) >= limit:
                break
        return merged

    def get_buy_transactions(self, symbol):
        asset_id = self.find_asset_id(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO)
        if asset_id not in self.data:
            return []

        buys = []
        for index, tx in enumerate(self.data[asset_id].get("transactions", [])):
            if tx.get("type") == "buy":
                buys.append((index, tx))
        return buys

    def get_transactions(self, category=CATEGORY_ALL, asset_id=None, symbol=""):
        transactions = []
        if symbol or (category not in (CATEGORY_ALL, *CATEGORIES) and asset_id is None):
            legacy_symbol = symbol or category
            asset_id = self.find_asset_id(legacy_symbol, CATEGORY_CRYPTO, MARKET_CRYPTO)
            category = CATEGORY_ALL

        assets = self.data.values()
        if asset_id:
            assets = [self.data[asset_id]] if asset_id in self.data else []
        elif category != CATEGORY_ALL:
            assets = self.get_assets(category)

        for asset in assets:
            for index, tx in enumerate(asset.get("transactions", [])):
                transactions.append({
                    "asset_id": asset["asset_id"],
                    "category": asset.get("category", ""),
                    "market": asset.get("market", ""),
                    "market_label": MARKET_LABELS.get(asset.get("market", ""), asset.get("market", "")),
                    "symbol": asset.get("symbol", ""),
                    "name": asset.get("name", ""),
                    "currency": asset.get("currency", ""),
                    "index": index,
                    "type": tx.get("type", ""),
                    "date": tx.get("date", ""),
                    "amount": tx.get("amount", 0),
                    "price": tx.get("price", 0),
                    "total": tx.get("total", 0),
                    "fx_to_cny": tx.get("fx_to_cny", 1),
                    "total_cny": tx.get("total_cny", 0),
                })

        return sorted(transactions, key=lambda item: item["date"])

    def update_transaction_by_asset(self, asset_id, transaction_index, tx_type, amount, price, trade_date, fx_to_cny=None):
        if asset_id not in self.data:
            print("未找到该资产。")
            return False

        asset = self.data[asset_id]
        tx_type = tx_type.strip().lower()
        if tx_type not in {"buy", "sell"}:
            print("交易类型必须是 buy 或 sell。")
            return False

        if self.validate_trade(asset["symbol"], amount, price) is None:
            return False

        trade_date = self.normalize_trade_date(trade_date)
        if trade_date is None:
            return False

        transactions = asset.get("transactions", [])
        if transaction_index < 0 or transaction_index >= len(transactions):
            print("未找到该交易记录。")
            return False

        fx, fx_source, fx_estimated = self.resolve_fx(asset["currency"], fx_to_cny)
        total = amount * price
        new_transactions = [item.copy() for item in transactions]
        new_transactions[transaction_index] = {
            "type": tx_type,
            "date": trade_date,
            "amount": amount,
            "price": price,
            "total": total,
            "currency": asset["currency"],
            "fx_to_cny": fx,
            "fx_source": fx_source,
            "fx_estimated": fx_estimated,
            "total_cny": total * fx,
        }

        rebuilt = self.rebuild_asset(new_transactions)
        if rebuilt is None:
            print("修改失败：修改后账单会导致卖出数量超过持仓，原有账单未改变。")
            return False

        asset["transactions"] = new_transactions
        asset["quantity"], asset["total_cost"], asset["total_cost_cny"] = rebuilt
        self.save_data()
        print("交易记录已更新。")
        return True

    def update_transaction(self, symbol, transaction_index, tx_type, amount, price, trade_date):
        asset_id = self.find_asset_id(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO)
        return self.update_transaction_by_asset(asset_id, transaction_index, tx_type, amount, price, trade_date)

    def delete_transaction_by_asset(self, asset_id, transaction_index):
        if asset_id not in self.data:
            print("未找到该资产。")
            return False

        asset = self.data[asset_id]
        transactions = asset.get("transactions", [])
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

        asset["transactions"] = new_transactions
        asset["quantity"], asset["total_cost"], asset["total_cost_cny"] = rebuilt

        self.save_data()
        print("交易记录已删除。")
        return True

    def delete_transaction(self, symbol, transaction_index):
        asset_id = self.find_asset_id(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO)
        return self.delete_transaction_by_asset(asset_id, transaction_index)

    def buy_asset(self, category, market, symbol, amount, price, trade_date=None, name="", fx_to_cny=None):
        symbol = self.validate_trade(symbol, amount, price)
        if symbol is None:
            return False

        category = normalize_category(category)
        market = normalize_market(market, category)
        symbol = normalize_symbol(symbol, category, market)
        trade_date = self.normalize_trade_date(trade_date)
        if trade_date is None:
            return False

        asset_id = asset_id_for(category, market, symbol)
        if asset_id not in self.data:
            self.data[asset_id] = self.build_asset(category, market, symbol, name)
        elif name and name.strip():
            self.data[asset_id]["name"] = name.strip()

        asset = self.data[asset_id]
        fx, fx_source, fx_estimated = self.resolve_fx(asset["currency"], fx_to_cny)
        total = amount * price
        total_cny = total * fx

        asset["quantity"] += amount
        asset["total_cost"] += total
        asset["total_cost_cny"] += total_cny
        asset["transactions"].append({
            "type": "buy",
            "date": trade_date,
            "amount": amount,
            "price": price,
            "total": total,
            "currency": asset["currency"],
            "fx_to_cny": fx,
            "fx_source": fx_source,
            "fx_estimated": fx_estimated,
            "total_cny": total_cny,
        })

        self.save_data()
        print("买入记录已保存。")
        return True

    def buy(self, symbol, amount, price, trade_date=None):
        return self.buy_asset(CATEGORY_CRYPTO, MARKET_CRYPTO, symbol, amount, price, trade_date)

    def delete_buy_order(self, symbol, transaction_index):
        asset_id = self.find_asset_id(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO)
        if asset_id not in self.data:
            print("未找到该币种。")
            return False

        transactions = self.data[asset_id].get("transactions", [])
        if transaction_index < 0 or transaction_index >= len(transactions):
            print("未找到该买入订单。")
            return False

        if transactions[transaction_index].get("type") != "buy":
            print("只能删除买入订单。")
            return False

        return self.delete_transaction_by_asset(asset_id, transaction_index)

    def rebuild_asset(self, transactions):
        quantity = 0.0
        total_cost = 0.0
        total_cost_cny = 0.0

        for tx in transactions:
            amount = safe_float(tx.get("amount"))
            price = safe_float(tx.get("price"))
            if amount <= 0 or price <= 0:
                print("账单中存在无效数量或价格，无法重算持仓。")
                return None

            total = safe_float(tx.get("total"), amount * price) or amount * price

            if tx.get("type") == "buy":
                quantity += amount
                total_cost += total
                total_cost_cny += total
            elif tx.get("type") == "sell":
                if amount > quantity + 1e-12:
                    return None
                avg_cost = total_cost / quantity if quantity > 0 else 0.0
                avg_cost_cny = total_cost_cny / quantity if quantity > 0 else 0.0
                quantity -= amount
                total_cost -= avg_cost * amount
                total_cost_cny -= avg_cost_cny * amount
            else:
                print("账单中存在未知交易类型，无法重算持仓。")
                return None

            if abs(quantity) < 1e-12:
                quantity = 0.0
                total_cost = 0.0
                total_cost_cny = 0.0

        return quantity, total_cost, total_cost_cny

    def sell_asset(self, asset_id, amount, price, trade_date=None, fx_to_cny=None):
        if asset_id not in self.data:
            print("没有该资产持仓。")
            return False

        asset = self.data[asset_id]
        if self.validate_trade(asset["symbol"], amount, price) is None:
            return False

        trade_date = self.normalize_trade_date(trade_date)
        if trade_date is None:
            return False

        if amount > asset["quantity"] + 1e-12:
            print("卖出数量超过持仓。")
            return False

        fx, fx_source, fx_estimated = self.resolve_fx(asset["currency"], fx_to_cny)
        avg_cost = asset["total_cost"] / asset["quantity"] if asset["quantity"] > 0 else 0
        avg_cost_cny = asset["total_cost_cny"] / asset["quantity"] if asset["quantity"] > 0 else 0
        total = amount * price
        total_cny = total * fx

        asset["quantity"] -= amount
        asset["total_cost"] -= avg_cost * amount
        asset["total_cost_cny"] -= avg_cost_cny * amount
        asset["transactions"].append({
            "type": "sell",
            "date": trade_date,
            "amount": amount,
            "price": price,
            "total": total,
            "currency": asset["currency"],
            "fx_to_cny": fx,
            "fx_source": fx_source,
            "fx_estimated": fx_estimated,
            "total_cny": total_cny,
        })

        if abs(asset["quantity"]) < 1e-12:
            asset["quantity"] = 0.0
            asset["total_cost"] = 0.0
            asset["total_cost_cny"] = 0.0

        self.save_data()
        print("卖出记录已保存。")
        return True

    def sell(self, symbol, amount, price, trade_date=None):
        asset_id = self.find_asset_id(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO)
        return self.sell_asset(asset_id, amount, price, trade_date)

    def get_latest_quotes(self, assets=None):
        assets = assets if assets is not None else self.get_active_assets()
        quotes, errors = fetch_quotes_for_assets(assets)
        for asset in assets:
            quote = quotes.get(asset["asset_id"])
            quote_name = quote.get("name") if quote else ""
            if (
                quote_name
                and quote_name != asset.get("symbol")
                and asset.get("name") in ("", asset.get("symbol"))
            ):
                asset["name"] = quote["name"]
        for asset_id, error in errors.items():
            asset = self.data.get(asset_id)
            label = asset_label(asset) if asset else asset_id
            print(f"{label} 价格获取失败: {error}")
        return quotes

    def get_prices(self):
        return self.get_latest_quotes()

    def fetch_okx_price(self, symbol):
        from crypto_portfolio.market_data import fetch_okx_price
        return fetch_okx_price(symbol)

    def fetch_binance_price(self, symbol):
        from crypto_portfolio.market_data import fetch_binance_price
        return fetch_binance_price(symbol)

    def fetch_coingecko_price(self, symbol):
        from crypto_portfolio.market_data import fetch_coingecko_price
        return fetch_coingecko_price(symbol)

    def fetch_asset_quote(self, asset):
        return fetch_asset_quote(asset)

    def format_quantity(self, quantity):
        return f"{quantity:.8f}".rstrip("0").rstrip(".")

    def format_money(self, value):
        return f"{value:.2f}"

    def build_holdings_snapshot(self, quotes, category_filter=CATEGORY_ALL):
        rows = []
        assets_snapshot = []
        total_value = 0.0
        total_profit = 0.0
        total_cost_for_priced_assets = 0.0
        unknown_price_symbols = []
        category_totals = {
            category: {
                "total_value": 0.0,
                "total_cost": 0.0,
                "total_profit": 0.0,
                "total_profit_rate": 0.0,
            }
            for category in CATEGORIES
        }

        assets = self.get_assets(category_filter, active_only=True)
        for asset in assets:
            asset_id = asset["asset_id"]
            quantity = asset["quantity"]
            total_cost = asset["total_cost"]
            avg_cost = total_cost / quantity if quantity > 0 else 0.0
            quote = quotes.get(asset_id) if quotes else None
            label = asset_label(asset)

            if not quote or quote.get("price") is None:
                unknown_price_symbols.append(label)
                cost_cny_text = f"{total_cost:.2f}" if asset["currency"] == "CNY" else "无法计算"
                rows.append([
                    asset["category"],
                    MARKET_LABELS.get(asset["market"], asset["market"]),
                    asset["symbol"],
                    asset.get("name", asset["symbol"]),
                    self.format_quantity(quantity),
                    f"{avg_cost:.4f}",
                    "价格未知",
                    asset["currency"],
                    "无法计算",
                    "无法计算",
                    cost_cny_text,
                    "无法计算",
                    "无法计算",
                ])
                continue

            current_price = float(quote["price"])
            fx_to_cny = float(quote.get("fx_to_cny", 1.0))
            total_cost_cny = total_cost * fx_to_cny
            avg_cost_cny = avg_cost * fx_to_cny
            value_cny = quantity * current_price * fx_to_cny
            profit_cny = value_cny - total_cost_cny
            profit_rate = (profit_cny / total_cost_cny * 100) if total_cost_cny > 0 else 0.0

            total_value += value_cny
            total_profit += profit_cny
            total_cost_for_priced_assets += total_cost_cny

            totals = category_totals[asset["category"]]
            totals["total_value"] += value_cny
            totals["total_cost"] += total_cost_cny
            totals["total_profit"] += profit_cny

            display_name = quote.get("name") or asset.get("name", asset["symbol"])
            if display_name == asset["symbol"] and asset.get("name"):
                display_name = asset["name"]

            row = [
                asset["category"],
                MARKET_LABELS.get(asset["market"], asset["market"]),
                asset["symbol"],
                display_name,
                self.format_quantity(quantity),
                f"{avg_cost:.4f}",
                f"{current_price:.4f}",
                quote.get("currency", asset["currency"]),
                f"{fx_to_cny:.4f}",
                f"{value_cny:.2f}",
                f"{total_cost_cny:.2f}",
                f"{profit_cny:.2f}",
                f"{profit_rate:.2f}%",
            ]
            rows.append(row)
            assets_snapshot.append({
                "asset_id": asset_id,
                "label": label,
                "category": asset["category"],
                "market": asset["market"],
                "market_label": MARKET_LABELS.get(asset["market"], asset["market"]),
                "symbol": asset["symbol"],
                "name": display_name,
                "quantity": quantity,
                "avg_cost": avg_cost,
                "avg_cost_cny": avg_cost_cny,
                "price": current_price,
                "currency": quote.get("currency", asset["currency"]),
                "fx_to_cny": fx_to_cny,
                "value_cny": value_cny,
                "total_cost_cny": total_cost_cny,
                "profit_cny": profit_cny,
                "profit_rate": profit_rate,
            })

        for totals in category_totals.values():
            if totals["total_cost"] > 0:
                totals["total_profit_rate"] = totals["total_profit"] / totals["total_cost"] * 100

        total_profit_rate = (
            total_profit / total_cost_for_priced_assets * 100
            if total_cost_for_priced_assets > 0 else 0.0
        )

        return {
            "version": PORTFOLIO_VERSION,
            "saved_at": self.now(),
            "display_currency": "CNY",
            "rows": rows,
            "assets": assets_snapshot,
            "category_totals": category_totals,
            "total_value": total_value,
            "total_cost": total_cost_for_priced_assets,
            "total_profit": total_profit,
            "total_profit_rate": total_profit_rate,
            "unknown_price_symbols": unknown_price_symbols,
        }

    def build_portfolio_summary(self, quotes=None, category_filter=CATEGORY_ALL):
        if quotes is None:
            quotes = self.get_latest_quotes(self.get_assets(category_filter, active_only=True))
        snapshot = self.build_holdings_snapshot(quotes, category_filter)
        return {
            "total_value": snapshot["total_value"],
            "total_cost": snapshot["total_cost"],
            "total_profit": snapshot["total_profit"],
            "total_profit_rate": snapshot["total_profit_rate"],
            "category_totals": snapshot["category_totals"],
            "unknown_price_symbols": snapshot["unknown_price_symbols"],
        }

    def save_holdings_snapshot(self, snapshot):
        snapshot_dir = Path(HOLDING_SNAPSHOT_DIR)
        snapshot_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        snapshot_path = snapshot_dir / f"holdings_{timestamp}.json"
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

        return snapshot_path

    def print_holdings_table(self, rows):
        from tabulate import tabulate

        print(tabulate(
            rows,
            headers=[
                "类别", "市场", "代码", "名称", "数量", "成本价", "当前价",
                "币种", "汇率", "持仓价值(CNY)", "成本(CNY)", "总收益(CNY)", "收益率",
            ],
            tablefmt="grid",
            disable_numparse=True,
        ))

    def print_holdings_snapshot(self, snapshot):
        rows = snapshot["rows"]
        unknown_price_symbols = snapshot.get("unknown_price_symbols", [])

        self.print_holdings_table(rows)

        total_label = "可计算总持仓价值" if unknown_price_symbols else "总持仓价值"
        profit_label = "可计算总收益" if unknown_price_symbols else "总收益"
        print(f"\n{total_label}: ¥{snapshot['total_value']:.2f}")
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
        quotes = self.get_latest_quotes()
        snapshot = self.build_holdings_snapshot(quotes)
        self.print_holdings_snapshot(snapshot)
        snapshot_path = self.save_holdings_snapshot(snapshot)
        print(f"本次查询结果已保存: {snapshot_path}")

    def show_history(self, symbol=""):
        rows = []
        if symbol:
            asset_id = self.find_asset_id(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO)
            transactions = self.get_transactions(asset_id=asset_id)
        else:
            transactions = self.get_transactions()

        for tx in transactions:
            rows.append([
                tx["category"],
                tx["market_label"],
                tx["symbol"],
                tx["date"],
                "买入" if tx["type"] == "buy" else "卖出",
                tx["amount"] if tx["type"] == "buy" else -tx["amount"],
                tx["price"],
                tx["currency"],
                tx["fx_to_cny"],
                tx["total_cny"],
            ])

        if not rows:
            print("暂无交易记录。")
            return

        rows.sort(key=lambda x: x[3])
        from tabulate import tabulate

        print(tabulate(
            rows,
            headers=["类别", "市场", "代码", "日期", "类型", "数量", "价格", "币种", "汇率", "人民币金额"],
            tablefmt="grid",
            floatfmt=".6f",
        ))

    def show_distribution(self):
        if not self.data:
            print("暂无持仓。")
            return

        print("正在查询当前最新价格......")
        snapshot = self.build_holdings_snapshot(self.get_latest_quotes())
        if snapshot["total_value"] <= 0:
            print("可计算总资产为 0，无法展示资产分布。")
            if snapshot.get("unknown_price_symbols"):
                print(f"价格未知，未计入资产分布: {', '.join(snapshot['unknown_price_symbols'])}")
            return

        print("\n资产分布:")
        for asset in sorted(snapshot["assets"], key=lambda item: item["value_cny"], reverse=True):
            pct = asset["value_cny"] / snapshot["total_value"] * 100
            print(f"{asset['label']}: ¥{asset['value_cny']:.2f} ({pct:.1f}%)")

        print(f"总价值: ¥{snapshot['total_value']:.2f}")
        if snapshot.get("unknown_price_symbols"):
            print(f"价格未知，未计入资产分布: {', '.join(snapshot['unknown_price_symbols'])}")
