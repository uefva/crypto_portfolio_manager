"""HTTP handler factory and query parsers for the service API.

The routes preserve the existing price API and add the portfolio API without
changing response shapes used by current clients.
"""

import gzip
import json
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlparse

from crypto_portfolio.market_data import CATEGORY_ALL, normalize_category
from crypto_portfolio.server.config import GZIP_MIN_BYTES, load_config, should_log_response_payload
from crypto_portfolio.server.portfolio_store import PortfolioStore
from crypto_portfolio.server.price_store import PriceHistoryStore


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


