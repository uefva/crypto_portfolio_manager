import configparser
import json
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from crypto_portfolio.market_data import (
    CATEGORY_CRYPTO,
    CATEGORY_FUND,
    CATEGORY_STOCK,
    MARKET_CRYPTO,
    MARKET_FUND,
    MARKET_HK,
    MARKET_SH,
    MARKET_SZ,
    MARKET_US,
    asset_id_for,
    currency_for,
    fetch_fx_to_cny,
    fetch_quotes_for_assets,
    normalize_category,
    normalize_symbol,
)


DEFAULT_CONFIG_PATH = "server_config.ini"
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

    def asset_history(self, asset_ids=None, categories=None, start=None, end=None, limit=5000):
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
        query = f"""
            WITH selected_times AS (
                SELECT DISTINCT fetched_at
                FROM asset_price_history
                {where}
                ORDER BY fetched_at ASC
                {limit_clause}
            )
            SELECT aph.*
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
                payload = self.asset_row_to_payload(row)
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
                    {
                        "timestamp": row["fetched_at"],
                        "prices": {},
                        "price_cny": {},
                        "fx_to_cny": {},
                        "sources": {},
                    },
                )
                point["prices"][row["asset_id"]] = row["price"]
                point["price_cny"][row["asset_id"]] = row["price_cny"]
                point["fx_to_cny"][row["asset_id"]] = row["fx_to_cny"]
                point["sources"][row["asset_id"]] = row["source"]

        return {
            "assets": assets,
            "points": list(points_by_time.values()),
        }

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


def make_handler(config_path, collector):
    class PriceRequestHandler(BaseHTTPRequestHandler):
        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            config = load_config(config_path)
            store = PriceHistoryStore(config.database)

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
                )
                self.send_json(payload)
                return

            self.send_json({"error": "not found"}, status=404)

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/refresh":
                try:
                    self.send_json(collector.fetch_once())
                except Exception as exc:
                    self.send_json({"error": str(exc)}, status=500)
                return

            self.send_json({"error": "not found"}, status=404)

        def send_json(self, payload, status=200):
            config = load_config(config_path)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
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
