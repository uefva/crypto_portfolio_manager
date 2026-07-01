import configparser
import gzip
import json
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from shutil import copy2
from urllib.parse import parse_qs, unquote, urlparse

from crypto_portfolio.market_data import (
    CATEGORY_ALL,
    CATEGORY_CRYPTO,
    CATEGORY_FUND,
    CATEGORY_STOCK,
    CATEGORIES,
    MARKET_CRYPTO,
    MARKET_FUND,
    MARKET_HK,
    MARKET_LABELS,
    MARKET_SH,
    MARKET_SZ,
    MARKET_US,
    asset_id_for,
    asset_label,
    currency_for,
    fetch_fx_to_cny,
    fetch_quotes_for_assets,
    normalize_category,
    normalize_market,
    normalize_symbol,
)


DEFAULT_CONFIG_PATH = "server_config.ini"
GZIP_MIN_BYTES = 1024
LOG_LEVELS = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
}


@dataclass
class ServerConfig:
    host: str
    port: int
    symbols: list[str]
    assets: list[dict]
    enabled_categories: dict
    asset_counts: dict
    interval_minutes: int
    database: str
    fetch_retries: int
    retry_backoff_seconds: float
    log_level: str


def parse_config_list(value):
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def read_csv(parser, section, option, fallback=""):
    return parse_config_list(parser.get(section, option, fallback=fallback))


def make_config_asset(category, market, symbol):
    symbol = normalize_symbol(symbol, category, market)
    return {
        "asset_id": asset_id_for(category, market, symbol),
        "category": category,
        "market": market,
        "symbol": symbol,
        "name": symbol,
        "currency": currency_for(category, market),
        "quantity": 1.0,
    }


def log_level_value(level):
    return LOG_LEVELS.get(str(level or "INFO").strip().upper(), LOG_LEVELS["INFO"])


def should_log(config, level):
    return log_level_value(level) >= log_level_value(getattr(config, "log_level", "INFO"))


def server_log(config, level, message):
    if not should_log(config, level):
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {level.upper()} {message}", flush=True)


def format_config_counts(config):
    return (
        f"crypto={config.asset_counts.get(CATEGORY_CRYPTO, 0)} "
        f"fund={config.asset_counts.get(CATEGORY_FUND, 0)} "
        f"stock={config.asset_counts.get(CATEGORY_STOCK, 0)}"
    )


def format_enabled_categories(config):
    return (
        f"crypto={bool(config.enabled_categories.get(CATEGORY_CRYPTO))} "
        f"fund={bool(config.enabled_categories.get(CATEGORY_FUND))} "
        f"stock={bool(config.enabled_categories.get(CATEGORY_STOCK))}"
    )


def format_asset_preview(assets, limit=30):
    asset_ids = [asset.get("asset_id", "") for asset in assets]
    preview = asset_ids[:limit]
    suffix = f", ... +{len(asset_ids) - limit}" if len(asset_ids) > limit else ""
    return ",".join(preview) + suffix


def load_config(config_path=DEFAULT_CONFIG_PATH):
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    legacy_symbols = parser.get("prices", "symbols", fallback="BTC,ETH")
    crypto_enabled = parser.getboolean("crypto", "enabled", fallback=True)
    fund_enabled = parser.getboolean("fund", "enabled", fallback=False)
    stock_enabled = parser.getboolean("stock", "enabled", fallback=False)

    crypto_symbols = read_csv(parser, "crypto", "symbols", fallback=legacy_symbols)
    crypto_symbols = [normalize_symbol(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO) for symbol in crypto_symbols]

    fund_codes = read_csv(parser, "fund", "codes", fallback="")
    stock_symbols = {
        MARKET_US: read_csv(parser, "stock", "us", fallback=""),
        MARKET_HK: read_csv(parser, "stock", "hk", fallback=""),
        MARKET_SH: read_csv(parser, "stock", "sh", fallback=""),
        MARKET_SZ: read_csv(parser, "stock", "sz", fallback=""),
    }

    assets = []
    if crypto_enabled:
        assets.extend(
            make_config_asset(CATEGORY_CRYPTO, MARKET_CRYPTO, symbol)
            for symbol in crypto_symbols
        )
    if fund_enabled:
        assets.extend(
            make_config_asset(CATEGORY_FUND, MARKET_FUND, code)
            for code in fund_codes
        )
    if stock_enabled:
        for market, symbols in stock_symbols.items():
            assets.extend(
                make_config_asset(CATEGORY_STOCK, market, symbol)
                for symbol in symbols
            )

    asset_counts = {
        CATEGORY_CRYPTO: sum(1 for asset in assets if asset["category"] == CATEGORY_CRYPTO),
        CATEGORY_FUND: sum(1 for asset in assets if asset["category"] == CATEGORY_FUND),
        CATEGORY_STOCK: sum(1 for asset in assets if asset["category"] == CATEGORY_STOCK),
    }

    return ServerConfig(
        host=parser.get("server", "host", fallback="127.0.0.1"),
        port=parser.getint("server", "port", fallback=8765),
        symbols=crypto_symbols,
        assets=assets,
        enabled_categories={
            CATEGORY_CRYPTO: crypto_enabled,
            CATEGORY_FUND: fund_enabled,
            CATEGORY_STOCK: stock_enabled,
        },
        asset_counts=asset_counts,
        interval_minutes=parser.getint("prices", "interval_minutes", fallback=30),
        database=parser.get("prices", "database", fallback="price_history.sqlite3"),
        fetch_retries=max(parser.getint("prices", "fetch_retries", fallback=3), 0),
        retry_backoff_seconds=max(parser.getfloat("prices", "retry_backoff_seconds", fallback=2.0), 0.0),
        log_level=parser.get("logging", "level", fallback="INFO").strip().upper(),
    )


def should_log_response_payload(config):
    return should_log(config, "DEBUG")


def safe_float(value, default=0.0):
    try:
        if value in (None, "", "-", "--"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_trade_date(trade_date=None):
    if trade_date is None or str(trade_date).strip() == "":
        return now_text()

    text = str(trade_date).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    raise ValueError("日期格式无效，请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS。")


def format_quantity(quantity):
    return f"{quantity:.8f}".rstrip("0").rstrip(".")


def series_value(profit, cost, metric):
    if metric == "收益率":
        return profit / cost * 100 if cost > 0 else 0.0
    return profit


class PriceHistoryStore:
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
                CREATE TABLE IF NOT EXISTS asset_price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    price REAL NOT NULL,
                    fx_to_cny REAL NOT NULL,
                    price_cny REAL NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    UNIQUE(asset_id, fetched_at)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_asset_price_history_asset_time
                ON asset_price_history(asset_id, fetched_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_asset_price_history_category_time
                ON asset_price_history(category, fetched_at)
            """)
            self.migrate_legacy_price_history(conn)
            conn.commit()

    def legacy_price_history_exists(self, conn):
        row = conn.execute("""
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'price_history'
            LIMIT 1
        """).fetchone()
        return row is not None

    def migrate_legacy_price_history(self, conn):
        if not self.legacy_price_history_exists(conn):
            return 0

        row = conn.execute("""
            SELECT 1
            FROM price_history ph
            LEFT JOIN asset_price_history aph
              ON aph.asset_id = 'crypto:CRYPTO:' || UPPER(ph.symbol)
             AND aph.fetched_at = ph.fetched_at
            WHERE aph.id IS NULL
            LIMIT 1
        """).fetchone()
        if row is None:
            return 0

        fx_to_cny, _fx_source, _fx_estimated = fetch_fx_to_cny("USD")
        rows = []
        for row in conn.execute("""
            SELECT symbol, price, source, fetched_at
            FROM price_history
            WHERE symbol IS NOT NULL AND price IS NOT NULL AND fetched_at IS NOT NULL
        """):
            symbol = normalize_symbol(row[0], CATEGORY_CRYPTO, MARKET_CRYPTO)
            asset_id = asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, symbol)
            price = float(row[1])
            rows.append((
                asset_id,
                CATEGORY_CRYPTO,
                MARKET_CRYPTO,
                symbol,
                symbol,
                "USD",
                price,
                float(fx_to_cny),
                price * float(fx_to_cny),
                row[2] or "legacy",
                row[3],
            ))

        if not rows:
            return 0

        conn.executemany("""
            INSERT OR IGNORE INTO asset_price_history(
                asset_id, category, market, symbol, name, currency,
                price, fx_to_cny, price_cny, source, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        return len(rows)

    def save_asset_quotes(self, quotes, assets_by_id, fetched_at):
        if not quotes:
            return 0

        rows = []
        for asset_id, quote in quotes.items():
            asset = assets_by_id.get(asset_id, {})
            rows.append((
                asset_id,
                quote.get("category") or asset.get("category", ""),
                quote.get("market") or asset.get("market", ""),
                quote.get("symbol") or asset.get("symbol", ""),
                quote.get("name") or asset.get("name") or quote.get("symbol") or "",
                quote.get("currency") or asset.get("currency", ""),
                float(quote["price"]),
                float(quote.get("fx_to_cny", 1.0)),
                float(quote.get("price_cny", quote["price"])),
                quote.get("source", "unknown"),
                fetched_at,
            ))

        with closing(self.connect()) as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO asset_price_history(
                    asset_id, category, market, symbol, name, currency,
                    price, fx_to_cny, price_cny, source, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            conn.commit()
        return len(rows)

    def latest_prices(self, symbols):
        if not symbols:
            return {}

        latest = {}
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            for symbol in symbols:
                symbol = normalize_symbol(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO)
                row = conn.execute("""
                    SELECT symbol, price, source, fetched_at
                    FROM asset_price_history
                    WHERE category = ? AND market = ? AND symbol = ?
                    ORDER BY fetched_at DESC
                    LIMIT 1
                """, (CATEGORY_CRYPTO, MARKET_CRYPTO, symbol)).fetchone()
                if row:
                    latest[symbol] = {
                        "price": row["price"],
                        "source": row["source"],
                        "fetched_at": row["fetched_at"],
                    }
        return latest

    def latest_asset_prices(self, asset_ids=None, categories=None):
        filters, params = self.asset_filters(asset_ids, categories)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        query = f"""
            SELECT aph.*
            FROM asset_price_history aph
            JOIN (
                SELECT asset_id, MAX(fetched_at) AS latest_at
                FROM asset_price_history
                {where}
                GROUP BY asset_id
            ) latest
            ON aph.asset_id = latest.asset_id AND aph.fetched_at = latest.latest_at
            ORDER BY aph.category ASC, aph.market ASC, aph.symbol ASC
        """
        latest = {}
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(query, params):
                latest[row["asset_id"]] = self.asset_row_to_payload(row)
        return latest

    def history(self, symbols, start=None, end=None, limit=2000):
        if not symbols:
            return []

        symbols = [normalize_symbol(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO) for symbol in symbols]
        placeholders = ",".join("?" for _ in symbols)
        params = [CATEGORY_CRYPTO, MARKET_CRYPTO, *symbols]
        filters = ["category = ?", "market = ?", f"symbol IN ({placeholders})"]
        if start:
            filters.append("fetched_at >= ?")
            params.append(start)
        if end:
            filters.append("fetched_at <= ?")
            params.append(end)
        params.append(limit)

        query = f"""
            SELECT symbol, price, source, fetched_at
            FROM asset_price_history
            WHERE {' AND '.join(filters)}
            ORDER BY fetched_at ASC, symbol ASC
            LIMIT ?
        """

        points_by_time = {}
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(query, params):
                point = points_by_time.setdefault(
                    row["fetched_at"],
                    {"timestamp": row["fetched_at"], "prices": {}, "sources": {}},
                )
                point["prices"][row["symbol"]] = row["price"]
                point["sources"][row["symbol"]] = row["source"]

        return list(points_by_time.values())

    def asset_history(self, asset_ids=None, categories=None, start=None, end=None, limit=5000, compact=True):
        filters, params = self.asset_filters(asset_ids, categories)
        if start:
            filters.append("fetched_at >= ?")
            params.append(start)
        if end:
            filters.append("fetched_at <= ?")
            params.append(end)

        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        limit_clause = "LIMIT ?" if limit and limit > 0 else ""
        time_params = list(params)
        if limit_clause:
            time_params.append(limit)

        outer_filters, outer_params = self.asset_filters(asset_ids, categories, prefix="aph.")
        outer_where = f"WHERE {' AND '.join(outer_filters)}" if outer_filters else ""
        query_params = time_params + outer_params
        selected_columns = (
            "aph.asset_id, aph.fetched_at, aph.price_cny"
            if compact
            else "aph.*"
        )
        query = f"""
            WITH selected_times AS (
                SELECT DISTINCT fetched_at
                FROM asset_price_history
                {where}
                ORDER BY fetched_at ASC
                {limit_clause}
            )
            SELECT {selected_columns}
            FROM asset_price_history aph
            JOIN selected_times st ON aph.fetched_at = st.fetched_at
            {outer_where}
            ORDER BY aph.fetched_at ASC, aph.asset_id ASC
        """

        assets = {}
        points_by_time = {}
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(query, query_params):
                if not compact:
                    assets[row["asset_id"]] = {
                        "asset_id": row["asset_id"],
                        "category": row["category"],
                        "market": row["market"],
                        "symbol": row["symbol"],
                        "name": row["name"],
                        "currency": row["currency"],
                    }
                point = points_by_time.setdefault(
                    row["fetched_at"],
                    self.empty_asset_history_point(row["fetched_at"], compact),
                )
                point["price_cny"][row["asset_id"]] = row["price_cny"]
                if not compact:
                    point["prices"][row["asset_id"]] = row["price"]
                    point["fx_to_cny"][row["asset_id"]] = row["fx_to_cny"]
                    point["sources"][row["asset_id"]] = row["source"]

        payload = {"points": list(points_by_time.values())}
        if not compact:
            payload["assets"] = assets
        return payload

    def empty_asset_history_point(self, fetched_at, compact=True):
        point = {
            "timestamp": fetched_at,
            "price_cny": {},
        }
        if not compact:
            point.update({
                "prices": {},
                "fx_to_cny": {},
                "sources": {},
            })
        return point

    def asset_filters(self, asset_ids=None, categories=None, prefix=""):
        filters = []
        params = []
        if asset_ids:
            placeholders = ",".join("?" for _ in asset_ids)
            filters.append(f"{prefix}asset_id IN ({placeholders})")
            params.extend(asset_ids)
        if categories:
            placeholders = ",".join("?" for _ in categories)
            filters.append(f"{prefix}category IN ({placeholders})")
            params.extend(categories)
        return filters, params

    def asset_row_to_payload(self, row):
        return {
            "asset_id": row["asset_id"],
            "category": row["category"],
            "market": row["market"],
            "symbol": row["symbol"],
            "name": row["name"],
            "currency": row["currency"],
            "price": row["price"],
            "fx_to_cny": row["fx_to_cny"],
            "price_cny": row["price_cny"],
            "source": row["source"],
            "fetched_at": row["fetched_at"],
        }


class PortfolioStore:
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

    def export_portfolio(self):
        assets = {
            asset["asset_id"]: {
                "asset_id": asset["asset_id"],
                "category": asset["category"],
                "market": asset["market"],
                "symbol": asset["symbol"],
                "name": asset["name"],
                "currency": asset["currency"],
                "quantity": asset["quantity"],
                "total_cost": asset["total_cost"],
                "total_cost_cny": asset["total_cost_cny"],
                "transactions": [
                    {
                        "id": tx.get("id"),
                        "type": tx.get("type"),
                        "date": tx.get("date"),
                        "amount": tx.get("amount"),
                        "price": tx.get("price"),
                        "total": tx.get("total"),
                        "currency": tx.get("currency", asset["currency"]),
                    }
                    for tx in asset.get("transactions", [])
                ],
            }
            for asset in self.get_assets()
        }
        return {"version": 2, "assets": assets, "exported_at": now_text()}

    def import_portfolio(self, payload):
        raw = payload.get("portfolio", payload) if isinstance(payload, dict) else {}
        if not isinstance(raw, dict):
            raise ValueError("导入内容必须是 JSON 对象。")

        report = {
            "assets_imported": 0,
            "assets_updated": 0,
            "transactions_imported": 0,
            "transactions_skipped": 0,
            "conflicts": [],
            "skipped": [],
            "backup_path": self.backup_local_portfolio_file(),
        }
        if raw.get("version") == 2 and isinstance(raw.get("assets"), dict):
            source_assets = raw["assets"].values()
        else:
            source_assets = []
            for symbol, legacy_asset in raw.items():
                if isinstance(legacy_asset, dict):
                    item = legacy_asset.copy()
                    item.update({
                        "category": CATEGORY_CRYPTO,
                        "market": MARKET_CRYPTO,
                        "symbol": symbol,
                        "name": symbol,
                        "currency": "USD",
                    })
                    source_assets.append(item)

        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            for source in source_assets:
                try:
                    asset = self.normalize_asset_input(
                        source.get("category", CATEGORY_CRYPTO),
                        source.get("market"),
                        source.get("symbol") or source.get("asset_id"),
                        source.get("name", ""),
                    )
                except ValueError as exc:
                    report["skipped"].append({"asset": source, "reason": str(exc)})
                    continue
                existing = conn.execute(
                    "SELECT asset_id FROM portfolio_assets WHERE asset_id = ?",
                    (asset["asset_id"],),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE portfolio_assets SET name = ?, updated_at = ? WHERE asset_id = ?",
                        (asset["name"], now_text(), asset["asset_id"]),
                    )
                    report["assets_updated"] += 1
                else:
                    timestamp = now_text()
                    conn.execute("""
                        INSERT INTO portfolio_assets(
                            asset_id, category, market, symbol, name, currency, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        asset["asset_id"], asset["category"], asset["market"], asset["symbol"],
                        asset["name"], asset["currency"], timestamp, timestamp,
                    ))
                    report["assets_imported"] += 1

                for tx in source.get("transactions", []):
                    if not isinstance(tx, dict):
                        report["transactions_skipped"] += 1
                        continue
                    try:
                        tx_type, amount, price, date, total = self.validate_transaction_payload(tx)
                    except ValueError as exc:
                        report["skipped"].append({"asset_id": asset["asset_id"], "transaction": tx, "reason": str(exc)})
                        report["transactions_skipped"] += 1
                        continue
                    duplicate = conn.execute("""
                        SELECT 1
                        FROM portfolio_transactions
                        WHERE asset_id = ? AND type = ? AND date = ?
                          AND ABS(amount - ?) < 0.000000001
                          AND ABS(price - ?) < 0.000000001
                        LIMIT 1
                    """, (asset["asset_id"], tx_type, date, amount, price)).fetchone()
                    if duplicate:
                        report["transactions_skipped"] += 1
                        continue
                    timestamp = now_text()
                    conn.execute("""
                        INSERT INTO portfolio_transactions(
                            asset_id, type, date, amount, price, total, currency, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        asset["asset_id"], tx_type, date, amount, price, total,
                        asset["currency"], timestamp, timestamp,
                    ))
                    report["transactions_imported"] += 1
            conn.commit()
        return report

    def backup_local_portfolio_file(self):
        source = Path("portfolio.json")
        if not source.exists():
            return ""
        backup_dir = Path("portfolio_backups")
        backup_dir.mkdir(exist_ok=True)
        backup_path = backup_dir / f"portfolio_server_import_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
        copy2(source, backup_path)
        return str(backup_path)


class PriceCollector:
    def __init__(self, config_path=DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.stop_event = threading.Event()

    def collect_assets(self, config):
        return list(config.assets)

    def fetch_assets_with_retries(self, assets, config):
        quotes = {}
        errors = {}
        pending_assets = list(assets)
        max_attempts = 1 + max(config.fetch_retries, 0)

        for attempt in range(1, max_attempts + 1):
            attempt_quotes, attempt_errors = fetch_quotes_for_assets(pending_assets, max_workers=24)
            pending_ids = {
                asset["asset_id"]
                for asset in pending_assets
            }

            for asset_id, quote in attempt_quotes.items():
                quotes[asset_id] = quote

            missing_ids = pending_ids - set(attempt_quotes)
            errors = {
                asset_id: attempt_errors.get(asset_id, "missing quote")
                for asset_id in missing_ids
            }
            pending_assets = [
                asset for asset in pending_assets
                if asset["asset_id"] in errors
            ]

            if not pending_assets:
                return quotes, {}

            server_log(
                config,
                "WARNING",
                (
                    "COLLECT retry "
                    f"attempt={attempt}/{max_attempts} "
                    f"missing={len(pending_assets)} "
                    f"ids={format_asset_preview(pending_assets)}"
                ),
            )
            if attempt < max_attempts and config.retry_backoff_seconds > 0:
                time.sleep(config.retry_backoff_seconds)

        return quotes, errors

    def fetch_once(self):
        config = load_config(self.config_path)
        assets = self.collect_assets(config)
        fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        assets_by_id = {asset["asset_id"]: asset for asset in assets}
        server_log(
            config,
            "INFO",
            (
                "COLLECT start "
                f"assets={len(assets)} "
                f"{format_config_counts(config)} "
                f"{format_enabled_categories(config)} "
                f"ids={format_asset_preview(assets)}"
            ),
        )
        if not assets:
            server_log(
                config,
                "WARNING",
                (
                    "COLLECT skipped reason=no_assets "
                    f"{format_config_counts(config)} "
                    f"{format_enabled_categories(config)}"
                ),
            )
            return {
                "status": "skipped",
                "reason": "no_assets",
                "fetched_at": fetched_at,
                "saved_count": 0,
                "missing_assets": [],
                "prices": {},
                "assets": {},
            }

        quotes, errors = self.fetch_assets_with_retries(assets, config)
        for asset_id, error in sorted(errors.items()):
            asset = assets_by_id.get(asset_id, {})
            server_log(
                config,
                "WARNING",
                (
                    "COLLECT failed "
                    f"asset_id={asset_id} "
                    f"category={asset.get('category', '')} "
                    f"market={asset.get('market', '')} "
                    f"symbol={asset.get('symbol', '')} "
                    f"error={error}"
                ),
            )
        for asset_id, quote in sorted(quotes.items()):
            server_log(
                config,
                "DEBUG",
                (
                    "COLLECT quote "
                    f"asset_id={asset_id} "
                    f"price={quote.get('price')} "
                    f"currency={quote.get('currency')} "
                    f"price_cny={quote.get('price_cny')} "
                    f"source={quote.get('source')}"
                ),
            )

        store = PriceHistoryStore(config.database)
        if errors or len(quotes) != len(assets):
            missing_assets = sorted(errors or (set(assets_by_id) - set(quotes)))
            server_log(
                config,
                "ERROR",
                (
                    "COLLECT finish "
                    f"status=failed "
                    f"saved=0/{len(assets)} "
                    f"missing={len(missing_assets)} "
                    f"database={config.database}"
                ),
            )
            return {
                "status": "failed",
                "reason": "missing_assets",
                "fetched_at": fetched_at,
                "saved_count": 0,
                "missing_assets": missing_assets,
                "errors": errors,
                "prices": {
                    asset_id: quote["price"]
                    for asset_id, quote in quotes.items()
                },
                "assets": {
                    asset_id: {
                        "category": quote.get("category"),
                        "market": quote.get("market"),
                        "symbol": quote.get("symbol"),
                        "name": quote.get("name"),
                        "currency": quote.get("currency"),
                        "price_cny": quote.get("price_cny"),
                    }
                    for asset_id, quote in quotes.items()
                },
            }

        saved_count = store.save_asset_quotes(quotes, assets_by_id, fetched_at)

        status = "saved" if saved_count == len(assets) else "partial"
        if saved_count == 0:
            status = "skipped"

        server_log(
            config,
            "INFO",
            (
                "COLLECT finish "
                f"status={status} "
                f"saved={saved_count}/{len(assets)} "
                f"missing={len(errors)} "
                f"database={config.database}"
            ),
        )

        return {
            "status": status,
            "fetched_at": fetched_at,
            "saved_count": saved_count,
            "missing_assets": sorted(errors),
            "errors": errors,
            "prices": {
                asset_id: quote["price"]
                for asset_id, quote in quotes.items()
            },
            "assets": {
                asset_id: {
                    "category": quote.get("category"),
                    "market": quote.get("market"),
                    "symbol": quote.get("symbol"),
                    "name": quote.get("name"),
                    "currency": quote.get("currency"),
                    "price_cny": quote.get("price_cny"),
                }
                for asset_id, quote in quotes.items()
            },
        }

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        return thread

    def stop(self):
        self.stop_event.set()

    def run(self):
        while not self.stop_event.is_set():
            try:
                result = self.fetch_once()
                missing = result.get("missing_assets", [])
                missing_text = f"，缺失 {len(missing)} 个资产" if missing else ""
                print(f"{result['fetched_at']} 价格采集 {result['status']}，保存 {result['saved_count']} 条{missing_text}。")
            except Exception as exc:
                print(f"价格采集失败: {exc}")

            config = load_config(self.config_path)
            interval_seconds = max(config.interval_minutes, 1) * 60
            self.stop_event.wait(interval_seconds)


def normalize_symbols(symbols):
    return [symbol.strip().upper() for symbol in symbols if symbol.strip()]


def fetch_prices(symbols):
    symbols = normalize_symbols(symbols)
    if not symbols:
        return {}, {}

    assets = [
        {
            "asset_id": asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, symbol),
            "category": CATEGORY_CRYPTO,
            "market": MARKET_CRYPTO,
            "symbol": symbol,
            "name": symbol,
            "currency": "USD",
        }
        for symbol in symbols
    ]
    quotes, _errors = fetch_quotes_for_assets(assets)
    prices = {}
    sources = {}
    for quote in quotes.values():
        prices[quote["symbol"]] = quote["price"]
        sources[quote["symbol"]] = quote.get("source", "unknown")
    return prices, sources


def parse_csv(query, key):
    raw = query.get(key, [""])[0]
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_symbols(query, default_symbols):
    symbols = parse_csv(query, "symbols")
    return [symbol.upper() for symbol in symbols] if symbols else default_symbols


def parse_asset_ids(query):
    return parse_csv(query, "asset_ids")


def parse_categories(query):
    values = parse_csv(query, "categories") or parse_csv(query, "category")
    return [normalize_category(value) for value in values]


def parse_limit(query, default=5000):
    raw = str(query.get("limit", [str(default)])[0]).strip().lower()
    if raw in {"", "all", "none", "0"}:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def parse_bool(query, key, default=False):
    raw = str(query.get(key, [str(default)])[0]).strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


def make_handler(config_path, collector):
    class PriceRequestHandler(BaseHTTPRequestHandler):
        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            config = load_config(config_path)
            store = PriceHistoryStore(config.database)
            portfolio = PortfolioStore(config.database)

            if parsed.path == "/api/health":
                self.send_json({
                    "status": "ok",
                    "symbols": config.symbols,
                    "asset_count": len(config.assets),
                    "enabled_categories": config.enabled_categories,
                    "asset_counts": config.asset_counts,
                })
                return

            if parsed.path == "/api/symbols":
                self.send_json({"symbols": config.symbols})
                return

            if parsed.path == "/api/prices/latest":
                symbols = parse_symbols(query, config.symbols)
                self.send_json({"prices": store.latest_prices(symbols)})
                return

            if parsed.path == "/api/prices/history":
                symbols = parse_symbols(query, config.symbols)
                limit = int(query.get("limit", ["2000"])[0])
                points = store.history(
                    symbols,
                    start=query.get("start", [None])[0],
                    end=query.get("end", [None])[0],
                    limit=limit,
                )
                self.send_json({"symbols": symbols, "points": points})
                return

            if parsed.path == "/api/assets/latest":
                self.send_json({
                    "prices": store.latest_asset_prices(
                        asset_ids=parse_asset_ids(query),
                        categories=parse_categories(query),
                    )
                })
                return

            if parsed.path == "/api/assets/history":
                limit = parse_limit(query, default=5000)
                payload = store.asset_history(
                    asset_ids=parse_asset_ids(query),
                    categories=parse_categories(query),
                    start=query.get("start", [None])[0],
                    end=query.get("end", [None])[0],
                    limit=limit,
                    compact=not parse_bool(query, "full", default=False),
                )
                self.send_json(payload)
                return

            if parsed.path == "/api/portfolio/assets":
                self.send_api_data(portfolio.get_assets(
                    category=query.get("category", [CATEGORY_ALL])[0],
                    active_only=parse_bool(query, "active_only", default=False),
                ))
                return

            if parsed.path == "/api/portfolio/transactions":
                asset_id = query.get("asset_id", [None])[0]
                self.send_api_data(portfolio.get_transactions(
                    category=query.get("category", [CATEGORY_ALL])[0],
                    asset_id=asset_id,
                ))
                return

            if parsed.path == "/api/portfolio/holdings":
                try:
                    self.send_api_data(portfolio.build_holdings_snapshot(
                        category_filter=query.get("category", [CATEGORY_ALL])[0],
                    ))
                except Exception as exc:
                    self.send_api_error(str(exc), status=500, code="holdings_failed")
                return

            if parsed.path == "/api/portfolio/summary":
                try:
                    self.send_api_data(portfolio.build_summary(
                        category_filter=query.get("category", [CATEGORY_ALL])[0],
                    ))
                except Exception as exc:
                    self.send_api_error(str(exc), status=500, code="summary_failed")
                return

            if parsed.path == "/api/portfolio/profit-history":
                try:
                    self.send_api_data(portfolio.build_profit_history(
                        store,
                        metric=query.get("metric", ["收益金额"])[0],
                        range_start=query.get("start", [None])[0],
                    ))
                except Exception as exc:
                    self.send_api_error(str(exc), status=500, code="profit_history_failed")
                return

            if parsed.path == "/api/portfolio/export":
                self.send_api_data(portfolio.export_portfolio())
                return

            self.send_json({"error": "not found"}, status=404)

        def do_POST(self):
            parsed = urlparse(self.path)
            config = load_config(config_path)
            portfolio = PortfolioStore(config.database)
            if parsed.path == "/api/refresh":
                try:
                    self.send_json(collector.fetch_once())
                except Exception as exc:
                    self.send_json({"error": str(exc)}, status=500)
                return

            if parsed.path == "/api/portfolio/assets":
                try:
                    payload = self.read_json_body()
                    self.send_api_data(portfolio.upsert_asset(
                        payload.get("category"),
                        payload.get("market"),
                        payload.get("symbol"),
                        payload.get("name", ""),
                    ), status=201)
                except ValueError as exc:
                    self.send_api_error(str(exc), status=400, code="invalid_asset")
                except Exception as exc:
                    self.send_api_error(str(exc), status=500, code="asset_save_failed")
                return

            if parsed.path == "/api/portfolio/transactions":
                try:
                    self.send_api_data(portfolio.add_transaction(self.read_json_body()), status=201)
                except ValueError as exc:
                    self.send_api_error(str(exc), status=400, code="invalid_transaction")
                except Exception as exc:
                    self.send_api_error(str(exc), status=500, code="transaction_save_failed")
                return

            if parsed.path == "/api/portfolio/import":
                try:
                    self.send_api_data(portfolio.import_portfolio(self.read_json_body()))
                except ValueError as exc:
                    self.send_api_error(str(exc), status=400, code="invalid_import")
                except Exception as exc:
                    self.send_api_error(str(exc), status=500, code="import_failed")
                return

            self.send_json({"error": "not found"}, status=404)

        def do_PUT(self):
            parsed = urlparse(self.path)
            config = load_config(config_path)
            portfolio = PortfolioStore(config.database)
            parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
            try:
                if len(parts) == 4 and parts[:3] == ["api", "portfolio", "assets"]:
                    payload = self.read_json_body()
                    self.send_api_data(portfolio.update_asset(
                        parts[3],
                        payload.get("category"),
                        payload.get("market"),
                        payload.get("symbol"),
                        payload.get("name", ""),
                    ))
                    return
                if len(parts) == 4 and parts[:3] == ["api", "portfolio", "transactions"]:
                    self.send_api_data(portfolio.update_transaction(int(parts[3]), self.read_json_body()))
                    return
            except ValueError as exc:
                self.send_api_error(str(exc), status=400, code="invalid_request")
                return
            except Exception as exc:
                self.send_api_error(str(exc), status=500, code="update_failed")
                return
            self.send_json({"error": "not found"}, status=404)

        def do_DELETE(self):
            parsed = urlparse(self.path)
            config = load_config(config_path)
            portfolio = PortfolioStore(config.database)
            parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
            try:
                if len(parts) == 4 and parts[:3] == ["api", "portfolio", "assets"]:
                    self.send_api_data(portfolio.delete_asset(parts[3]))
                    return
                if len(parts) == 4 and parts[:3] == ["api", "portfolio", "transactions"]:
                    self.send_api_data(portfolio.delete_transaction(int(parts[3])))
                    return
            except ValueError as exc:
                self.send_api_error(str(exc), status=400, code="invalid_request")
                return
            except Exception as exc:
                self.send_api_error(str(exc), status=500, code="delete_failed")
                return
            self.send_json({"error": "not found"}, status=404)

        def read_json_body(self):
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            body = self.rfile.read(length).decode("utf-8")
            if not body.strip():
                return {}
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise ValueError("请求体不是有效 JSON。") from exc
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是 JSON 对象。")
            return payload

        def send_api_data(self, data, status=200):
            self.send_json({"data": data}, status=status)

        def send_api_error(self, message, status=400, code="error"):
            self.send_json({"error": {"code": code, "message": message}}, status=status)

        def send_json(self, payload, status=200):
            config = load_config(config_path)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            compressed = self.should_gzip(body)
            if compressed:
                body = gzip.compress(body)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if compressed:
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            if should_log_response_payload(config):
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                client_ip = self.get_client_ip()
                print(
                    f"[{timestamp}] DEBUG response to {client_ip} "
                    f"{self.path} status={status}: "
                    f"{json.dumps(payload, ensure_ascii=False)}"
                )

        def should_gzip(self, body):
            if len(body) < GZIP_MIN_BYTES:
                return False
            accept_encoding = self.headers.get("Accept-Encoding", "")
            return "gzip" in accept_encoding.lower()

        def get_client_ip(self):
            for header in ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For"):
                value = self.headers.get(header)
                if value:
                    return value.split(",")[0].strip()
            return self.client_address[0]

        def log_message(self, format, *args):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            client_ip = self.get_client_ip()
            peer_ip = self.client_address[0]
            peer_info = f" (peer {peer_ip})" if client_ip != peer_ip else ""
            print(f"[{timestamp}] {client_ip}{peer_info} - {format % args}")

    return PriceRequestHandler


def run_server(config_path=DEFAULT_CONFIG_PATH):
    config = load_config(config_path)
    PriceHistoryStore(config.database)
    PortfolioStore(config.database)
    collector = PriceCollector(config_path)
    collector.start()

    server = ThreadingHTTPServer(
        (config.host, config.port),
        make_handler(config_path, collector),
    )
    print(f"价格服务已启动: http://{config.host}:{config.port}")
    print(f"配置文件: {config_path}")
    print(f"日志等级: {config.log_level}")
    server_log(
        config,
        "INFO",
        (
            "CONFIG loaded "
            f"path={config_path} "
            f"database={config.database} "
            f"interval_minutes={config.interval_minutes} "
            f"{format_config_counts(config)} "
            f"{format_enabled_categories(config)} "
            f"ids={format_asset_preview(config.assets)}"
        ),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("正在停止价格服务...")
    finally:
        collector.stop()
        server.server_close()


def main():
    run_server()
