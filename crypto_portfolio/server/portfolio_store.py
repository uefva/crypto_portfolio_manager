"""SQLite portfolio storage and service-side portfolio calculations.

Orders are stored in native asset currency. CNY cost and profit are derived from
current or historical FX rates when holdings and charts are queried.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

from crypto_portfolio.market_data import (
    CATEGORIES,
    CATEGORY_ALL,
    CATEGORY_CRYPTO,
    MARKET_CRYPTO,
    MARKET_LABELS,
    asset_id_for,
    asset_label,
    currency_for,
    fetch_quotes_for_assets,
    normalize_category,
    normalize_market,
    normalize_symbol,
)
from crypto_portfolio.server.utils import (
    format_quantity,
    normalize_trade_date,
    now_text,
    safe_float,
    series_value,
)
from crypto_portfolio.server.portfolio_import_export import PortfolioImportExportMixin


class PortfolioStore(PortfolioImportExportMixin):
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        if self.database_path.parent != Path("."):
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self):
        return sqlite3.connect(self.database_path)

    def init_schema(self):
        with closing(self.connect()) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_assets (
                    asset_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    date TEXT NOT NULL,
                    amount REAL NOT NULL,
                    price REAL NOT NULL,
                    total REAL NOT NULL,
                    currency TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(asset_id) REFERENCES portfolio_assets(asset_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_portfolio_transactions_asset_date
                ON portfolio_transactions(asset_id, date)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO portfolio_meta(key, value, updated_at)
                VALUES ('schema_version', '1', ?)
            """, (now_text(),))
            conn.commit()

    def normalize_asset_input(self, category, market, symbol, name=""):
        category = normalize_category(category)
        market = normalize_market(market, category)
        symbol = normalize_symbol(symbol, category, market)
        if not symbol:
            raise ValueError("代码不能为空。")
        asset_id = asset_id_for(category, market, symbol)
        name = str(name or "").strip() or symbol
        currency = currency_for(category, market)
        return {
            "asset_id": asset_id,
            "category": category,
            "market": market,
            "symbol": symbol,
            "name": name,
            "currency": currency,
        }

    def row_to_asset(self, row, transactions=None):
        asset = {
            "asset_id": row["asset_id"],
            "category": row["category"],
            "market": row["market"],
            "symbol": row["symbol"],
            "name": row["name"],
            "currency": row["currency"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "transactions": transactions or [],
        }
        rebuilt = self.rebuild_asset(asset["transactions"])
        if rebuilt is None:
            asset["quantity"] = 0.0
            asset["total_cost"] = 0.0
            asset["total_cost_cny"] = 0.0
            asset["invalid_transactions"] = True
        else:
            asset["quantity"], asset["total_cost"], asset["total_cost_cny"] = rebuilt
            asset["invalid_transactions"] = False
        return asset

    def row_to_transaction(self, row, asset=None, index=None):
        tx = {
            "id": row["id"],
            "asset_id": row["asset_id"],
            "type": row["type"],
            "date": row["date"],
            "amount": row["amount"],
            "price": row["price"],
            "total": row["total"],
            "currency": row["currency"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if index is not None:
            tx["index"] = index
        if asset:
            tx.update({
                "category": asset.get("category", ""),
                "market": asset.get("market", ""),
                "market_label": MARKET_LABELS.get(asset.get("market", ""), asset.get("market", "")),
                "symbol": asset.get("symbol", ""),
                "name": asset.get("name", ""),
            })
        return tx

    def load_assets_by_id(self, conn):
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT *
            FROM portfolio_assets
            ORDER BY category ASC, market ASC, symbol ASC
        """).fetchall()
        tx_rows = conn.execute("""
            SELECT *
            FROM portfolio_transactions
            ORDER BY date ASC, id ASC
        """).fetchall()
        tx_by_asset = {}
        for row in tx_rows:
            tx_by_asset.setdefault(row["asset_id"], []).append(self.row_to_transaction(row))

        assets = {}
        for row in rows:
            assets[row["asset_id"]] = self.row_to_asset(row, tx_by_asset.get(row["asset_id"], []))
        return assets

    def get_assets(self, category=CATEGORY_ALL, active_only=False):
        category = normalize_category(category) if category != CATEGORY_ALL else CATEGORY_ALL
        with closing(self.connect()) as conn:
            assets = list(self.load_assets_by_id(conn).values())
        filtered = []
        for asset in assets:
            if category != CATEGORY_ALL and asset["category"] != category:
                continue
            if active_only and asset.get("quantity", 0) <= 0:
                continue
            filtered.append(asset)
        return filtered

    def get_asset(self, asset_id):
        with closing(self.connect()) as conn:
            return self.load_assets_by_id(conn).get(asset_id)

    def upsert_asset(self, category, market, symbol, name=""):
        asset = self.normalize_asset_input(category, market, symbol, name)
        timestamp = now_text()
        with closing(self.connect()) as conn:
            existing = conn.execute(
                "SELECT asset_id, created_at FROM portfolio_assets WHERE asset_id = ?",
                (asset["asset_id"],),
            ).fetchone()
            created_at = existing[1] if existing else timestamp
            conn.execute("""
                INSERT OR REPLACE INTO portfolio_assets(
                    asset_id, category, market, symbol, name, currency, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                asset["asset_id"],
                asset["category"],
                asset["market"],
                asset["symbol"],
                asset["name"],
                asset["currency"],
                created_at,
                timestamp,
            ))
            conn.commit()
        return self.get_asset(asset["asset_id"])

    def update_asset(self, old_asset_id, category, market, symbol, name=""):
        asset = self.normalize_asset_input(category, market, symbol, name)
        timestamp = now_text()
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT * FROM portfolio_assets WHERE asset_id = ?",
                (old_asset_id,),
            ).fetchone()
            if not existing:
                raise ValueError("未找到该资产。")

            tx_count = conn.execute(
                "SELECT COUNT(*) FROM portfolio_transactions WHERE asset_id = ?",
                (old_asset_id,),
            ).fetchone()[0]
            changing_identity = (
                asset["category"] != existing["category"]
                or asset["market"] != existing["market"]
                or asset["symbol"] != existing["symbol"]
            )
            if tx_count > 0 and changing_identity:
                raise ValueError("已有交易记录的资产只能修改名称。")

            if old_asset_id != asset["asset_id"]:
                conflict = conn.execute(
                    "SELECT 1 FROM portfolio_assets WHERE asset_id = ?",
                    (asset["asset_id"],),
                ).fetchone()
                if conflict:
                    raise ValueError("目标资产已存在。")

            if old_asset_id == asset["asset_id"]:
                conn.execute("""
                    UPDATE portfolio_assets
                    SET category = ?, market = ?, symbol = ?, name = ?, currency = ?, updated_at = ?
                    WHERE asset_id = ?
                """, (
                    asset["category"],
                    asset["market"],
                    asset["symbol"],
                    asset["name"],
                    asset["currency"],
                    timestamp,
                    old_asset_id,
                ))
            else:
                conn.execute("""
                    INSERT INTO portfolio_assets(
                        asset_id, category, market, symbol, name, currency, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    asset["asset_id"],
                    asset["category"],
                    asset["market"],
                    asset["symbol"],
                    asset["name"],
                    asset["currency"],
                    existing["created_at"],
                    timestamp,
                ))
                conn.execute("DELETE FROM portfolio_assets WHERE asset_id = ?", (old_asset_id,))
            conn.commit()
        return self.get_asset(asset["asset_id"])

    def delete_asset(self, asset_id):
        with closing(self.connect()) as conn:
            tx_count = conn.execute(
                "SELECT COUNT(*) FROM portfolio_transactions WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()[0]
            if tx_count > 0:
                raise ValueError("该资产已有交易记录，不能直接删除。")
            deleted = conn.execute("DELETE FROM portfolio_assets WHERE asset_id = ?", (asset_id,)).rowcount
            if not deleted:
                raise ValueError("未找到该资产。")
            conn.commit()
        return {"asset_id": asset_id, "deleted": True}

    def get_transactions(self, category=CATEGORY_ALL, asset_id=None):
        category = normalize_category(category) if category != CATEGORY_ALL else CATEGORY_ALL
        with closing(self.connect()) as conn:
            assets = self.load_assets_by_id(conn)
        transactions = []
        for asset in assets.values():
            if asset_id and asset["asset_id"] != asset_id:
                continue
            if category != CATEGORY_ALL and asset["category"] != category:
                continue
            for index, tx in enumerate(asset.get("transactions", [])):
                transactions.append(self.row_to_transaction(tx, asset, index))
        return sorted(transactions, key=lambda item: (item["date"], item["id"]))

    def validate_transaction_payload(self, payload):
        tx_type = str(payload.get("type", "")).strip().lower()
        if tx_type not in {"buy", "sell"}:
            raise ValueError("交易类型必须是 buy 或 sell。")
        amount = safe_float(payload.get("amount"))
        price = safe_float(payload.get("price"))
        if amount <= 0 or price <= 0:
            raise ValueError("数量和价格必须大于 0。")
        date = normalize_trade_date(payload.get("date"))
        total = amount * price
        return tx_type, amount, price, date, total

    def add_transaction(self, payload):
        category = normalize_category(payload.get("category", CATEGORY_CRYPTO))
        market = normalize_market(payload.get("market"), category)
        symbol = normalize_symbol(payload.get("symbol"), category, market)
        name = payload.get("name") or symbol
        asset_id = payload.get("asset_id") or asset_id_for(category, market, symbol)
        tx_type, amount, price, date, total = self.validate_transaction_payload(payload)
        timestamp = now_text()

        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            assets = self.load_assets_by_id(conn)
            asset = assets.get(asset_id)
            if asset is None:
                if tx_type != "buy":
                    raise ValueError("没有该资产持仓。")
                asset = self.normalize_asset_input(category, market, symbol, name)
                conn.execute("""
                    INSERT INTO portfolio_assets(
                        asset_id, category, market, symbol, name, currency, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    asset["asset_id"],
                    asset["category"],
                    asset["market"],
                    asset["symbol"],
                    asset["name"],
                    asset["currency"],
                    timestamp,
                    timestamp,
                ))
            elif name and name.strip() and asset["name"] in ("", asset["symbol"]):
                conn.execute(
                    "UPDATE portfolio_assets SET name = ?, updated_at = ? WHERE asset_id = ?",
                    (name.strip(), timestamp, asset_id),
                )
                asset["name"] = name.strip()

            currency = asset["currency"]
            new_transactions = [item.copy() for item in asset.get("transactions", [])]
            new_transactions.append({
                "type": tx_type,
                "date": date,
                "amount": amount,
                "price": price,
                "total": total,
            })
            if self.rebuild_asset(new_transactions) is None:
                raise ValueError("交易会导致卖出数量超过持仓。")

            cursor = conn.execute("""
                INSERT INTO portfolio_transactions(
                    asset_id, type, date, amount, price, total, currency, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (asset_id, tx_type, date, amount, price, total, currency, timestamp, timestamp))
            conn.commit()
            transaction_id = cursor.lastrowid
        return self.transaction_by_id(transaction_id)

    def transaction_by_id(self, transaction_id):
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            assets = self.load_assets_by_id(conn)
            row = conn.execute(
                "SELECT * FROM portfolio_transactions WHERE id = ?",
                (transaction_id,),
            ).fetchone()
            if not row:
                return None
            asset = assets.get(row["asset_id"], {})
            asset_transactions = asset.get("transactions", [])
            index = next(
                (idx for idx, tx in enumerate(asset_transactions) if tx.get("id") == transaction_id),
                None,
            )
            return self.row_to_transaction(row, asset, index)

    def update_transaction(self, transaction_id, payload):
        tx_type, amount, price, date, total = self.validate_transaction_payload(payload)
        timestamp = now_text()
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            assets = self.load_assets_by_id(conn)
            row = conn.execute(
                "SELECT * FROM portfolio_transactions WHERE id = ?",
                (transaction_id,),
            ).fetchone()
            if not row:
                raise ValueError("未找到该交易记录。")
            asset = assets.get(row["asset_id"])
            if not asset:
                raise ValueError("交易所属资产不存在。")

            new_transactions = []
            for tx in asset.get("transactions", []):
                item = tx.copy()
                if item.get("id") == transaction_id:
                    item.update({
                        "type": tx_type,
                        "date": date,
                        "amount": amount,
                        "price": price,
                        "total": total,
                    })
                new_transactions.append(item)
            if self.rebuild_asset(new_transactions) is None:
                raise ValueError("修改后账单会导致卖出数量超过持仓。")

            conn.execute("""
                UPDATE portfolio_transactions
                SET type = ?, date = ?, amount = ?, price = ?, total = ?, updated_at = ?
                WHERE id = ?
            """, (tx_type, date, amount, price, total, timestamp, transaction_id))
            conn.commit()
        return self.transaction_by_id(transaction_id)

    def delete_transaction(self, transaction_id):
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            assets = self.load_assets_by_id(conn)
            row = conn.execute(
                "SELECT * FROM portfolio_transactions WHERE id = ?",
                (transaction_id,),
            ).fetchone()
            if not row:
                raise ValueError("未找到该交易记录。")
            asset = assets.get(row["asset_id"])
            new_transactions = [
                tx for tx in asset.get("transactions", [])
                if tx.get("id") != transaction_id
            ]
            if self.rebuild_asset(new_transactions) is None:
                raise ValueError("删除后账单会导致卖出数量超过持仓。")
            conn.execute("DELETE FROM portfolio_transactions WHERE id = ?", (transaction_id,))
            conn.commit()
        return {"id": transaction_id, "deleted": True}

    def rebuild_asset(self, transactions):
        quantity = 0.0
        total_cost = 0.0
        total_cost_cny = 0.0
        for tx in transactions:
            amount = safe_float(tx.get("amount"))
            price = safe_float(tx.get("price"))
            if amount <= 0 or price <= 0:
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
                return None
            if abs(quantity) < 1e-12:
                quantity = 0.0
                total_cost = 0.0
                total_cost_cny = 0.0
        return quantity, total_cost, total_cost_cny

    def build_holdings_snapshot(self, category_filter=CATEGORY_ALL):
        assets = self.get_assets(category_filter, active_only=True)
        quotes, errors = fetch_quotes_for_assets(assets)
        return self.snapshot_from_quotes(assets, quotes, errors, category_filter)

    def snapshot_from_quotes(self, assets, quotes, errors=None, category_filter=CATEGORY_ALL):
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
        for asset in assets:
            asset_id = asset["asset_id"]
            quantity = asset.get("quantity", 0.0)
            total_cost = asset.get("total_cost", 0.0)
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
                    format_quantity(quantity),
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
            rows.append([
                asset["category"],
                MARKET_LABELS.get(asset["market"], asset["market"]),
                asset["symbol"],
                display_name,
                format_quantity(quantity),
                f"{avg_cost:.4f}",
                f"{current_price:.4f}",
                quote.get("currency", asset["currency"]),
                f"{fx_to_cny:.4f}",
                f"{value_cny:.2f}",
                f"{total_cost_cny:.2f}",
                f"{profit_cny:.2f}",
                f"{profit_rate:.2f}%",
            ])
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
            "version": 1,
            "saved_at": now_text(),
            "display_currency": "CNY",
            "rows": rows,
            "assets": assets_snapshot,
            "category_totals": category_totals,
            "total_value": total_value,
            "total_cost": total_cost_for_priced_assets,
            "total_profit": total_profit,
            "total_profit_rate": total_profit_rate,
            "unknown_price_symbols": unknown_price_symbols,
            "errors": errors or {},
        }

    def build_summary(self, category_filter=CATEGORY_ALL):
        snapshot = self.build_holdings_snapshot(category_filter)
        return {
            "total_value": snapshot["total_value"],
            "total_cost": snapshot["total_cost"],
            "total_profit": snapshot["total_profit"],
            "total_profit_rate": snapshot["total_profit_rate"],
            "category_totals": snapshot["category_totals"],
            "unknown_price_symbols": snapshot["unknown_price_symbols"],
        }

    def build_profit_history(self, price_store, metric="收益金额", range_start=None):
        holdings = {
            asset["asset_id"]: asset
            for asset in self.get_assets(active_only=True)
            if asset.get("quantity", 0) > 0 and asset.get("total_cost", 0) > 0
        }
        if not holdings:
            return {"labels": [], "series": {}, "all_series": {}, "series_meta": {}, "metric": metric, "source": "server"}
        history = price_store.asset_history(
            asset_ids=sorted(holdings),
            start=range_start,
            limit=0,
            compact=False,
        )
        labels = []
        series = {}
        meta = {"总资产": {"kind": "total"}}
        for point in history.get("points", []):
            timestamp = point.get("timestamp", "未知")
            price_cny = point.get("price_cny", {})
            fx_to_cny = point.get("fx_to_cny", {})
            point_index = len(labels)
            point_values = {}
            category_values = {
                category: {"profit": 0.0, "cost": 0.0, "has_value": False}
                for category in CATEGORIES
            }
            total_profit = 0.0
            total_cost = 0.0
            for asset_id, asset in holdings.items():
                price = price_cny.get(asset_id)
                fx = fx_to_cny.get(asset_id)
                if price is None or fx is None:
                    continue
                value = asset["quantity"] * float(price)
                cost = asset["total_cost"] * float(fx)
                profit = value - cost
                point_values[asset_id] = (profit, cost)
                total_profit += profit
                total_cost += cost
                bucket = category_values[asset["category"]]
                bucket["profit"] += profit
                bucket["cost"] += cost
                bucket["has_value"] = True
            if not point_values:
                continue
            labels.append(timestamp)
            series.setdefault("总资产", []).append((point_index, series_value(total_profit, total_cost, metric)))
            for category, bucket in category_values.items():
                if not bucket["has_value"]:
                    continue
                key = f"{category}合计"
                series.setdefault(key, []).append((point_index, series_value(bucket["profit"], bucket["cost"], metric)))
                meta[key] = {"kind": "category", "category": category}
            for asset_id, (profit, cost) in point_values.items():
                asset = holdings[asset_id]
                key = asset_label(asset)
                series.setdefault(key, []).append((point_index, series_value(profit, cost, metric)))
                meta[key] = {"kind": "asset", "category": asset["category"], "asset_id": asset_id}
        all_series = {key: points for key, points in series.items() if points}
        return {
            "labels": labels,
            "series": all_series,
            "all_series": all_series,
            "series_meta": meta,
            "metric": metric,
            "source": "server",
        }
