import configparser
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from crypto_portfolio.portfolio_manager import PortfolioManager


DEFAULT_CONFIG_PATH = "server_config.ini"


@dataclass
class ServerConfig:
    host: str
    port: int
    symbols: list[str]
    interval_minutes: int
    database: str


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
    )


class PriceHistoryStore:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        if self.database_path.parent != Path("."):
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self):
        return sqlite3.connect(self.database_path)

    def init_schema(self):
        with self.connect() as conn:
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

    def save_prices(self, prices, sources, fetched_at):
        if not prices:
            return 0

        rows = [
            (symbol, float(price), sources.get(symbol, "unknown"), fetched_at)
            for symbol, price in prices.items()
        ]
        with self.connect() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO price_history(symbol, price, source, fetched_at)
                VALUES (?, ?, ?, ?)
            """, rows)
        return len(rows)

    def latest_prices(self, symbols):
        if not symbols:
            return {}

        latest = {}
        with self.connect() as conn:
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
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(query, params):
                point = points_by_time.setdefault(
                    row["fetched_at"],
                    {"timestamp": row["fetched_at"], "prices": {}, "sources": {}}
                )
                point["prices"][row["symbol"]] = row["price"]
                point["sources"][row["symbol"]] = row["source"]

        return list(points_by_time.values())


class PriceCollector:
    def __init__(self, config_path=DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.stop_event = threading.Event()

    def fetch_once(self):
        config = load_config(self.config_path)
        fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prices, sources = fetch_prices(config.symbols)
        store = PriceHistoryStore(config.database)
        saved_count = store.save_prices(prices, sources, fetched_at)
        return {
            "fetched_at": fetched_at,
            "saved_count": saved_count,
            "prices": prices,
            "sources": sources,
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
                print(f"{result['fetched_at']} 已保存 {result['saved_count']} 条价格。")
            except Exception as exc:
                print(f"价格采集失败: {exc}")

            config = load_config(self.config_path)
            interval_seconds = max(config.interval_minutes, 1) * 60
            self.stop_event.wait(interval_seconds)


def fetch_prices(symbols):
    symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    if not symbols:
        return {}, {}

    manager = PortfolioManager()
    prices = {}
    sources = {}
    futures_by_symbol = {symbol: [] for symbol in symbols}
    future_meta = {}
    executor = ThreadPoolExecutor(max_workers=min(len(symbols) * 3, 24))

    try:
        for symbol in symbols:
            tasks = [
                ("OKX", manager.fetch_okx_price),
                ("Binance", manager.fetch_binance_price),
                ("CoinGecko", manager.fetch_coingecko_price),
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
            except Exception:
                continue

            if price is None:
                continue

            prices[symbol] = price
            sources[symbol] = source
            resolved_symbols.add(symbol)

            for other_future in futures_by_symbol[symbol]:
                if other_future is not future:
                    other_future.cancel()

            if len(resolved_symbols) == len(symbols):
                break
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return prices, sources


def parse_symbols(query, default_symbols):
    raw_symbols = query.get("symbols", [""])[0]
    if not raw_symbols:
        return default_symbols
    return [symbol.strip().upper() for symbol in raw_symbols.split(",") if symbol.strip()]


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
                self.send_json({"status": "ok", "symbols": config.symbols})
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
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            print(f"{self.address_string()} - {format % args}")

    return PriceRequestHandler


def run_server(config_path=DEFAULT_CONFIG_PATH):
    config = load_config(config_path)
    PriceHistoryStore(config.database)
    collector = PriceCollector(config_path)
    collector.start()

    server = ThreadingHTTPServer(
        (config.host, config.port),
        make_handler(config_path, collector)
    )
    print(f"价格服务已启动: http://{config.host}:{config.port}")
    print(f"配置文件: {config_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("正在停止价格服务...")
    finally:
        collector.stop()
        server.server_close()


def main():
    run_server()
