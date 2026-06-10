import configparser
import json
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from crypto_portfolio.market_data import (
    CATEGORY_CRYPTO,
    MARKET_CRYPTO,
    asset_id_for,
    fetch_quotes_for_assets,
    normalize_category,
    normalize_symbol,
)
from crypto_portfolio.portfolio_manager import PortfolioManager


DEFAULT_CONFIG_PATH = "server_config.ini"


@dataclass
class ServerConfig:
    host: str
    port: int
    symbols: list[str]
    interval_minutes: int
    database: str
    log_level: str


def load_config(config_path=DEFAULT_CONFIG_PATH):
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    symbols = parser.get("prices", "symbols", fallback="BTC,ETH").split(",")
    symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]

    return ServerConfig(
        host=parser.get("server", "host", fallback="127.0.0.1"),
        port=parser.getint("server", "port", fallback=8765),
        symbols=symbols,
        interval_minutes=parser.getint("prices", "interval_minutes", fallback=30),
        database=parser.get("prices", "database", fallback="price_history.sqlite3"),
        log_level=parser.get("logging", "level", fallback="INFO").strip().upper(),
    )


def should_log_response_payload(config):
    levels = {
        "TRACE": 5,
        "DEBUG": 10,
        "INFO": 20,
        "WARNING": 30,
        "ERROR": 40,
    }
    return levels.get(config.log_level, 20) <= levels["DEBUG"]


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
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    price REAL NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    UNIQUE(symbol, fetched_at)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_price_history_symbol_time
                ON price_history(symbol, fetched_at)
            """)
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
            conn.commit()

    def save_prices(self, prices, sources, fetched_at):
        if not prices:
            return 0

        rows = [
            (symbol, float(price), sources.get(symbol, "unknown"), fetched_at)
            for symbol, price in prices.items()
        ]
        with closing(self.connect()) as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO price_history(symbol, price, source, fetched_at)
                VALUES (?, ?, ?, ?)
            """, rows)
            conn.commit()
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
                row = conn.execute("""
                    SELECT symbol, price, source, fetched_at
                    FROM price_history
                    WHERE symbol = ?
                    ORDER BY fetched_at DESC
                    LIMIT 1
                """, (symbol,)).fetchone()
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

        placeholders = ",".join("?" for _ in symbols)
        params = list(symbols)
        filters = [f"symbol IN ({placeholders})"]
        if start:
            filters.append("fetched_at >= ?")
            params.append(start)
        if end:
            filters.append("fetched_at <= ?")
            params.append(end)
        params.append(limit)

        query = f"""
            SELECT symbol, price, source, fetched_at
            FROM price_history
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
        params.append(limit)

        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        query = f"""
            SELECT *
            FROM asset_price_history
            {where}
            ORDER BY fetched_at ASC, asset_id ASC
            LIMIT ?
        """

        assets = {}
        points_by_time = {}
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(query, params):
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

    def asset_filters(self, asset_ids=None, categories=None):
        filters = []
        params = []
        if asset_ids:
            placeholders = ",".join("?" for _ in asset_ids)
            filters.append(f"asset_id IN ({placeholders})")
            params.extend(asset_ids)
        if categories:
            placeholders = ",".join("?" for _ in categories)
            filters.append(f"category IN ({placeholders})")
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

    def configured_crypto_assets(self, config):
        assets = []
        for symbol in config.symbols:
            symbol = normalize_symbol(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO)
            assets.append({
                "asset_id": asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, symbol),
                "category": CATEGORY_CRYPTO,
                "market": MARKET_CRYPTO,
                "symbol": symbol,
                "name": symbol,
                "currency": "USD",
                "quantity": 1.0,
            })
        return assets

    def collect_assets(self, config):
        manager = PortfolioManager()
        assets = manager.get_active_assets()
        return assets or self.configured_crypto_assets(config)

    def fetch_once(self):
        config = load_config(self.config_path)
        assets = self.collect_assets(config)
        fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not assets:
            return {
                "status": "skipped",
                "reason": "no_assets",
                "fetched_at": fetched_at,
                "saved_count": 0,
                "missing_assets": [],
                "prices": {},
                "assets": {},
            }

        quotes, errors = fetch_quotes_for_assets(assets, max_workers=24)
        assets_by_id = {asset["asset_id"]: asset for asset in assets}
        store = PriceHistoryStore(config.database)
        saved_count = store.save_asset_quotes(quotes, assets_by_id, fetched_at)

        crypto_prices = {}
        crypto_sources = {}
        for asset_id, quote in quotes.items():
            if quote.get("category") != CATEGORY_CRYPTO:
                continue
            symbol = quote.get("symbol")
            crypto_prices[symbol] = quote["price"]
            crypto_sources[symbol] = quote.get("source", "unknown")
        store.save_prices(crypto_prices, crypto_sources, fetched_at)

        status = "saved" if saved_count == len(assets) else "partial"
        if saved_count == 0:
            status = "skipped"

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
                manager = PortfolioManager()
                self.send_json({
                    "status": "ok",
                    "symbols": config.symbols,
                    "asset_count": len(manager.get_active_assets()),
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
                limit = int(query.get("limit", ["5000"])[0])
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
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("正在停止价格服务...")
    finally:
        collector.stop()
        server.server_close()


def main():
    run_server()
