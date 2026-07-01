"""HTTP client for the service-backed portfolio API.

The desktop app uses this client first and falls back to the local JSON store
when the configured service is unavailable.
"""

import json
from pathlib import Path
from urllib.parse import quote

import requests

from crypto_portfolio.market_data import (
    CATEGORY_ALL,
    CATEGORY_CRYPTO,
    MARKET_CRYPTO,
    asset_id_for,
    currency_for,
    normalize_category,
    normalize_market,
    normalize_symbol,
)
from crypto_portfolio.portfolio.local_store import PortfolioManager


class PortfolioApiClient:
    def __init__(self, server_url, fallback=None, timeout=8):
        self.server_url = str(server_url or "").rstrip("/")
        self.timeout = timeout
        self.fallback = fallback or PortfolioManager()
        self.data = {}
        self.last_error = ""
        self.online = False

    def now(self):
        return self.fallback.now()

    def format_quantity(self, quantity):
        return self.fallback.format_quantity(quantity)

    def request(self, method, path, params=None, json_payload=None):
        if not self.server_url:
            raise ConnectionError("服务端地址为空")
        url = f"{self.server_url}{path}"
        response = requests.request(
            method,
            url,
            params=params,
            json=json_payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and "error" in payload:
            error = payload["error"]
            if isinstance(error, dict):
                raise ValueError(error.get("message") or error.get("code") or "服务端错误")
            raise ValueError(str(error))
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    def mark_error(self, exc):
        self.online = False
        self.last_error = str(exc)

    def load_data(self):
        try:
            data = self.request("GET", "/api/portfolio/export")
            self.data = data.get("assets", {})
            self.online = True
            self.last_error = ""
            return self.data
        except Exception as exc:
            self.mark_error(exc)
            self.data = self.fallback.load_data()
            return self.data

    def save_data(self):
        return None

    def refresh_cache_from_assets(self, assets):
        for asset in assets:
            self.data[asset["asset_id"]] = asset
        self.fallback.data = self.data

    def get_assets(self, category=CATEGORY_ALL, active_only=False):
        try:
            params = {"category": category, "active_only": "1" if active_only else "0"}
            assets = self.request("GET", "/api/portfolio/assets", params=params)
            self.data = {asset["asset_id"]: asset for asset in assets}
            self.fallback.data = self.data
            self.online = True
            self.last_error = ""
            return assets
        except Exception as exc:
            self.mark_error(exc)
            self.fallback.data = self.data or self.fallback.load_data()
            return self.fallback.get_assets(category, active_only=active_only)

    def get_active_assets(self):
        return self.get_assets(CATEGORY_ALL, active_only=True)

    def asset_suggestions(self, query="", category=CATEGORY_ALL, market=None, limit=20):
        self.fallback.data = self.data or self.fallback.load_data()
        return self.fallback.asset_suggestions(query, category, market, limit=limit)

    def find_asset_id(self, symbol, category=CATEGORY_CRYPTO, market=None):
        category = normalize_category(category)
        market = normalize_market(market, category)
        symbol = normalize_symbol(symbol, category, market)
        exact = asset_id_for(category, market, symbol)
        if exact in self.data:
            return exact
        self.fallback.data = self.data or self.fallback.load_data()
        return self.fallback.find_asset_id(symbol, category, market)

    def get_transactions(self, category=CATEGORY_ALL, asset_id=None, symbol=""):
        try:
            params = {"category": category}
            if asset_id:
                params["asset_id"] = asset_id
            elif symbol:
                params["asset_id"] = self.find_asset_id(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO)
            transactions = self.request("GET", "/api/portfolio/transactions", params=params)
            self.online = True
            self.last_error = ""
            return transactions
        except Exception as exc:
            self.mark_error(exc)
            self.fallback.data = self.data or self.fallback.load_data()
            return self.fallback.get_transactions(category=category, asset_id=asset_id, symbol=symbol)

    def upsert_asset(self, category, market, symbol, name=""):
        try:
            asset = self.request("POST", "/api/portfolio/assets", json_payload={
                "category": category,
                "market": market,
                "symbol": symbol,
                "name": name,
            })
            self.load_data()
            return asset
        except Exception as exc:
            self.mark_error(exc)
            return None

    def update_asset(self, old_asset_id, category, market, symbol, name=""):
        try:
            self.request(
                "PUT",
                f"/api/portfolio/assets/{quote(old_asset_id, safe='')}",
                json_payload={
                    "category": category,
                    "market": market,
                    "symbol": symbol,
                    "name": name,
                },
            )
            self.load_data()
            return True
        except Exception as exc:
            self.mark_error(exc)
            return False

    def delete_asset(self, asset_id):
        try:
            self.request("DELETE", f"/api/portfolio/assets/{quote(asset_id, safe='')}")
            self.load_data()
            return True
        except Exception as exc:
            self.mark_error(exc)
            return False

    def buy_asset(self, category, market, symbol, amount, price, trade_date=None, name="", fx_to_cny=None):
        return self.add_transaction(category, market, symbol, name, "buy", amount, price, trade_date)

    def sell_asset(self, asset_id, amount, price, trade_date=None, fx_to_cny=None):
        asset = self.data.get(asset_id)
        if asset is None:
            self.load_data()
            asset = self.data.get(asset_id)
        if asset is None:
            return False
        return self.add_transaction(
            asset["category"],
            asset["market"],
            asset["symbol"],
            asset.get("name", ""),
            "sell",
            amount,
            price,
            trade_date,
            asset_id=asset_id,
        )

    def add_transaction(self, category, market, symbol, name, tx_type, amount, price, trade_date=None, asset_id=None):
        try:
            self.request("POST", "/api/portfolio/transactions", json_payload={
                "asset_id": asset_id,
                "category": category,
                "market": market,
                "symbol": symbol,
                "name": name,
                "type": tx_type,
                "amount": amount,
                "price": price,
                "date": trade_date,
            })
            self.load_data()
            return True
        except Exception as exc:
            self.mark_error(exc)
            return False

    def transaction_id_for_index(self, asset_id, transaction_index):
        transactions = self.get_transactions(asset_id=asset_id)
        for tx in transactions:
            if int(tx.get("index", -1)) == int(transaction_index):
                return tx.get("id")
        return None

    def update_transaction_by_asset(self, asset_id, transaction_index, tx_type, amount, price, trade_date, fx_to_cny=None):
        transaction_id = self.transaction_id_for_index(asset_id, transaction_index)
        if transaction_id is None:
            return False
        try:
            self.request(
                "PUT",
                f"/api/portfolio/transactions/{transaction_id}",
                json_payload={
                    "type": tx_type,
                    "amount": amount,
                    "price": price,
                    "date": trade_date,
                },
            )
            self.load_data()
            return True
        except Exception as exc:
            self.mark_error(exc)
            return False

    def delete_transaction_by_asset(self, asset_id, transaction_index):
        transaction_id = self.transaction_id_for_index(asset_id, transaction_index)
        if transaction_id is None:
            return False
        try:
            self.request("DELETE", f"/api/portfolio/transactions/{transaction_id}")
            self.load_data()
            return True
        except Exception as exc:
            self.mark_error(exc)
            return False

    def get_latest_quotes(self, assets=None):
        return {}

    def build_holdings_snapshot(self, quotes=None, category_filter=CATEGORY_ALL):
        try:
            snapshot = self.request("GET", "/api/portfolio/holdings", params={"category": category_filter})
            self.online = True
            self.last_error = ""
            return snapshot
        except Exception as exc:
            self.mark_error(exc)
            self.fallback.data = self.data or self.fallback.load_data()
            return self.fallback.build_holdings_snapshot(quotes or {}, category_filter)

    def build_portfolio_summary(self, quotes=None, category_filter=CATEGORY_ALL):
        try:
            summary = self.request("GET", "/api/portfolio/summary", params={"category": category_filter})
            self.online = True
            self.last_error = ""
            return summary
        except Exception as exc:
            self.mark_error(exc)
            self.fallback.data = self.data or self.fallback.load_data()
            return self.fallback.build_portfolio_summary(quotes=quotes, category_filter=category_filter)

    def build_profit_history(self, metric="收益金额", start=None):
        return self.request("GET", "/api/portfolio/profit-history", params={
            "metric": metric,
            "start": start or "",
        })

    def import_local_portfolio(self, path="portfolio.json"):
        source = Path(path)
        if not source.exists():
            return None
        payload = source.read_text(encoding="utf-8")
        return self.request("POST", "/api/portfolio/import", json_payload={
            "portfolio": json.loads(payload)
        })

    def save_holdings_snapshot(self, snapshot):
        return self.fallback.save_holdings_snapshot(snapshot)

    def list_holdings_snapshots(self):
        return self.fallback.list_holdings_snapshots()
