import gzip
import json
import sqlite3
import tempfile
import unittest
from io import BytesIO
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

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
)
from crypto_portfolio.price_server import (
    GZIP_MIN_BYTES,
    PriceCollector,
    PriceHistoryStore,
    load_config,
    make_handler,
    parse_bool,
    parse_limit,
)


class PriceHistoryStoreTest(unittest.TestCase):
    def write_config(self, tmpdir, content):
        path = Path(tmpdir) / "server_config.ini"
        path.write_text(content, encoding="utf-8")
        return path

    def build_fake_handler(self, config_path, accept_encoding=""):
        handler_class = make_handler(str(config_path), collector=None)
        handler = handler_class.__new__(handler_class)
        handler.headers = {"Accept-Encoding": accept_encoding}
        handler.wfile = BytesIO()
        handler.path = "/api/test"
        handler.client_address = ("127.0.0.1", 12345)
        handler.sent_status = None
        handler.sent_headers = []
        handler.send_response = lambda status: setattr(handler, "sent_status", status)
        handler.send_header = lambda key, value: handler.sent_headers.append((key, value))
        handler.end_headers = lambda: None
        return handler

    def test_asset_latest_and_history_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PriceHistoryStore(str(Path(tmpdir) / "prices.sqlite3"))
            asset_id = asset_id_for(CATEGORY_STOCK, MARKET_US, "QQQM")
            asset = {
                "asset_id": asset_id,
                "category": CATEGORY_STOCK,
                "market": MARKET_US,
                "symbol": "QQQM",
                "name": "QQQM",
                "currency": "USD",
            }
            quote = {
                "category": CATEGORY_STOCK,
                "market": MARKET_US,
                "symbol": "QQQM",
                "name": "QQQM",
                "currency": "USD",
                "price": 100.0,
                "fx_to_cny": 7.1,
                "price_cny": 710.0,
                "source": "test",
            }

            saved = store.save_asset_quotes({asset_id: quote}, {asset_id: asset}, "2026-06-10 10:00:00")
            self.assertEqual(saved, 1)

            latest = store.latest_asset_prices(asset_ids=[asset_id])
            self.assertEqual(latest[asset_id]["price"], 100.0)
            self.assertEqual(latest[asset_id]["price_cny"], 710.0)

            history = store.asset_history(asset_ids=[asset_id], compact=False)
            self.assertEqual(history["assets"][asset_id]["symbol"], "QQQM")
            self.assertEqual(history["points"][0]["price_cny"][asset_id], 710.0)
            self.assertIn("prices", history["points"][0])

            compact_history = store.asset_history(asset_ids=[asset_id])
            self.assertNotIn("assets", compact_history)
            self.assertNotIn("prices", compact_history["points"][0])
            self.assertNotIn("fx_to_cny", compact_history["points"][0])
            self.assertNotIn("sources", compact_history["points"][0])
            self.assertEqual(compact_history["points"][0]["price_cny"][asset_id], 710.0)

    def test_legacy_price_history_is_migrated_to_asset_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "prices.sqlite3"
            conn = sqlite3.connect(database)
            try:
                conn.execute("""
                    CREATE TABLE price_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        price REAL NOT NULL,
                        source TEXT NOT NULL,
                        fetched_at TEXT NOT NULL,
                        UNIQUE(symbol, fetched_at)
                    )
                """)
                conn.execute("""
                    INSERT INTO price_history(symbol, price, source, fetched_at)
                    VALUES ('BTC', 100.0, 'legacy-test', '2026-06-10 10:00:00')
                """)
                conn.commit()
            finally:
                conn.close()

            with patch("crypto_portfolio.price_server.fetch_fx_to_cny", return_value=(7.0, "test-rate", False)):
                store = PriceHistoryStore(str(database))

            asset_id = asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, "BTC")
            latest = store.latest_asset_prices(asset_ids=[asset_id])
            self.assertEqual(latest[asset_id]["price"], 100.0)
            self.assertEqual(latest[asset_id]["price_cny"], 700.0)

            legacy_history = store.history(["BTC"])
            self.assertEqual(legacy_history[0]["prices"]["BTC"], 100.0)
            self.assertEqual(legacy_history[0]["sources"]["BTC"], "legacy-test")

    def test_asset_history_limit_counts_time_points_not_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "prices.sqlite3"
            store = PriceHistoryStore(str(database))
            assets = [
                (asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, "BTC"), CATEGORY_CRYPTO, MARKET_CRYPTO, "BTC", "BTC", "USD"),
                (asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, "ETH"), CATEGORY_CRYPTO, MARKET_CRYPTO, "ETH", "ETH", "USD"),
                (asset_id_for(CATEGORY_STOCK, MARKET_US, "QQQM"), CATEGORY_STOCK, MARKET_US, "QQQM", "QQQM", "USD"),
            ]
            start_at = datetime(2026, 1, 1, 0, 0, 0)
            rows = []
            for point_index in range(1000):
                fetched_at = (start_at + timedelta(minutes=point_index)).strftime("%Y-%m-%d %H:%M:%S")
                for asset_index, (asset_id, category, market, symbol, name, currency) in enumerate(assets):
                    price = 100.0 + point_index + asset_index
                    rows.append((
                        asset_id,
                        category,
                        market,
                        symbol,
                        name,
                        currency,
                        price,
                        7.0,
                        price * 7.0,
                        "test",
                        fetched_at,
                    ))

            conn = sqlite3.connect(database)
            try:
                conn.executemany("""
                    INSERT INTO asset_price_history(
                        asset_id, category, market, symbol, name, currency,
                        price, fx_to_cny, price_cny, source, fetched_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, rows)
                conn.commit()
            finally:
                conn.close()

            limited = store.asset_history(limit=500)
            self.assertEqual(len(limited["points"]), 500)
            self.assertEqual(len(limited["points"][0]["price_cny"]), 3)
            self.assertEqual(len(limited["points"][-1]["price_cny"]), 3)
            self.assertNotIn("assets", limited)
            self.assertNotIn("prices", limited["points"][0])

            all_points = store.asset_history(limit=0)
            self.assertEqual(len(all_points["points"]), 1000)

            btc_id = assets[0][0]
            filtered = store.asset_history(
                asset_ids=[btc_id],
                start=(start_at + timedelta(minutes=100)).strftime("%Y-%m-%d %H:%M:%S"),
                end=(start_at + timedelta(minutes=199)).strftime("%Y-%m-%d %H:%M:%S"),
                limit=0,
                compact=False,
            )
            self.assertEqual(len(filtered["points"]), 100)
            self.assertEqual(set(filtered["assets"]), {btc_id})
            self.assertTrue(all(set(point["price_cny"]) == {btc_id} for point in filtered["points"]))
            self.assertTrue(all(set(point["prices"]) == {btc_id} for point in filtered["points"]))

    def test_parse_limit_supports_unlimited_and_invalid_values(self):
        self.assertEqual(parse_limit({"limit": ["all"]}), 0)
        self.assertEqual(parse_limit({"limit": ["0"]}), 0)
        self.assertEqual(parse_limit({"limit": ["bad"]}, default=123), 123)
        self.assertEqual(parse_limit({"limit": ["500"]}), 500)

    def test_parse_bool_supports_full_flag_values(self):
        self.assertTrue(parse_bool({"full": ["1"]}, "full"))
        self.assertTrue(parse_bool({"full": ["true"]}, "full"))
        self.assertFalse(parse_bool({"full": ["0"]}, "full", default=True))
        self.assertFalse(parse_bool({"full": ["false"]}, "full", default=True))
        self.assertTrue(parse_bool({"full": ["unknown"]}, "full", default=True))

    def test_send_json_uses_gzip_for_large_supported_responses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self.write_config(tmpdir, "[prices]\ndatabase = price_history.sqlite3\n")
            handler = self.build_fake_handler(config_path, accept_encoding="br, gzip")
            payload = {"data": "x" * (GZIP_MIN_BYTES + 100)}

            handler.send_json(payload)

            headers = dict(handler.sent_headers)
            self.assertEqual(handler.sent_status, 200)
            self.assertEqual(headers["Content-Encoding"], "gzip")
            decoded = gzip.decompress(handler.wfile.getvalue()).decode("utf-8")
            self.assertEqual(json.loads(decoded), payload)
            self.assertEqual(int(headers["Content-Length"]), len(handler.wfile.getvalue()))

    def test_send_json_skips_gzip_for_small_or_unsupported_responses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self.write_config(tmpdir, "[prices]\ndatabase = price_history.sqlite3\n")
            payload = {"ok": True}

            small_handler = self.build_fake_handler(config_path, accept_encoding="gzip")
            small_handler.send_json(payload)
            self.assertNotIn("Content-Encoding", dict(small_handler.sent_headers))
            self.assertEqual(json.loads(small_handler.wfile.getvalue().decode("utf-8")), payload)

            plain_handler = self.build_fake_handler(config_path, accept_encoding="")
            plain_handler.send_json({"data": "x" * (GZIP_MIN_BYTES + 100)})
            self.assertNotIn("Content-Encoding", dict(plain_handler.sent_headers))

    def test_load_config_builds_assets_from_enabled_sections(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self.write_config(tmpdir, """
[server]
host = 127.0.0.1
port = 8765

[prices]
interval_minutes = 30
database = price_history.sqlite3

[crypto]
enabled = true
symbols = BTC,ETH

[fund]
enabled = true
codes = 270042

[stock]
enabled = true
us = QQQM
hk = 00700
sh = 600519
sz = 000001
""")

            config = load_config(str(config_path))
            asset_ids = {asset["asset_id"] for asset in config.assets}

            self.assertEqual(config.symbols, ["BTC", "ETH"])
            self.assertIn(asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, "BTC"), asset_ids)
            self.assertIn(asset_id_for(CATEGORY_FUND, MARKET_FUND, "270042"), asset_ids)
            self.assertIn(asset_id_for(CATEGORY_STOCK, MARKET_US, "QQQM"), asset_ids)
            self.assertIn(asset_id_for(CATEGORY_STOCK, MARKET_HK, "00700"), asset_ids)
            self.assertIn(asset_id_for(CATEGORY_STOCK, MARKET_SH, "600519"), asset_ids)
            self.assertIn(asset_id_for(CATEGORY_STOCK, MARKET_SZ, "000001"), asset_ids)
            self.assertEqual(config.asset_counts[CATEGORY_CRYPTO], 2)
            self.assertEqual(config.asset_counts[CATEGORY_FUND], 1)
            self.assertEqual(config.asset_counts[CATEGORY_STOCK], 4)

    def test_disabled_categories_do_not_enter_collection_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self.write_config(tmpdir, """
[prices]
interval_minutes = 30
database = price_history.sqlite3

[crypto]
enabled = false
symbols = BTC

[fund]
enabled = false
codes = 270042

[stock]
enabled = true
us = QQQM
""")

            config = load_config(str(config_path))
            asset_ids = {asset["asset_id"] for asset in config.assets}

            self.assertNotIn(asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, "BTC"), asset_ids)
            self.assertNotIn(asset_id_for(CATEGORY_FUND, MARKET_FUND, "270042"), asset_ids)
            self.assertIn(asset_id_for(CATEGORY_STOCK, MARKET_US, "QQQM"), asset_ids)
            self.assertFalse(config.enabled_categories[CATEGORY_CRYPTO])
            self.assertFalse(config.enabled_categories[CATEGORY_FUND])

    def test_legacy_prices_symbols_fallback_to_crypto_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self.write_config(tmpdir, """
[prices]
symbols = BTC,ETH
interval_minutes = 30
database = price_history.sqlite3
""")

            config = load_config(str(config_path))
            asset_ids = {asset["asset_id"] for asset in config.assets}

            self.assertEqual(config.symbols, ["BTC", "ETH"])
            self.assertIn(asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, "BTC"), asset_ids)
            self.assertIn(asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, "ETH"), asset_ids)
            self.assertEqual(config.asset_counts[CATEGORY_CRYPTO], 2)
            self.assertEqual(config.asset_counts[CATEGORY_FUND], 0)
            self.assertEqual(config.asset_counts[CATEGORY_STOCK], 0)

    def test_refresh_collects_configured_assets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "prices.sqlite3"
            config_path = self.write_config(tmpdir, f"""
[prices]
interval_minutes = 30
database = {database}
fetch_retries = 3
retry_backoff_seconds = 0

[crypto]
enabled = false
symbols = BTC

[fund]
enabled = true
codes = 270042

[stock]
enabled = true
us = QQQM
""")
            fund_id = asset_id_for(CATEGORY_FUND, MARKET_FUND, "270042")
            stock_id = asset_id_for(CATEGORY_STOCK, MARKET_US, "QQQM")

            def fake_fetch(assets, max_workers=16):
                quotes = {}
                for asset in assets:
                    quotes[asset["asset_id"]] = {
                        "category": asset["category"],
                        "market": asset["market"],
                        "symbol": asset["symbol"],
                        "name": asset["symbol"],
                        "currency": asset["currency"],
                        "price": 100.0,
                        "fx_to_cny": 1.0,
                        "price_cny": 100.0,
                        "source": "test",
                    }
                return quotes, {}

            with patch("crypto_portfolio.price_server.fetch_quotes_for_assets", fake_fetch):
                result = PriceCollector(str(config_path)).fetch_once()

            self.assertEqual(result["status"], "saved")
            self.assertEqual(result["saved_count"], 2)
            self.assertIn(fund_id, result["assets"])
            self.assertIn(stock_id, result["assets"])

            history = PriceHistoryStore(str(database)).asset_history(asset_ids=[fund_id, stock_id], compact=False)
            self.assertEqual(set(history["assets"]), {fund_id, stock_id})

    def test_refresh_retries_missing_assets_before_saving(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "prices.sqlite3"
            config_path = self.write_config(tmpdir, f"""
[prices]
interval_minutes = 30
database = {database}
fetch_retries = 2
retry_backoff_seconds = 0

[crypto]
enabled = true
symbols = BTC,ETH
""")
            btc_id = asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, "BTC")
            eth_id = asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, "ETH")
            calls = {"count": 0}

            def fake_fetch(assets, max_workers=16):
                calls["count"] += 1
                quotes = {}
                errors = {}
                for asset in assets:
                    if calls["count"] == 1 and asset["asset_id"] == eth_id:
                        errors[asset["asset_id"]] = "temporary failure"
                        continue
                    quotes[asset["asset_id"]] = {
                        "category": asset["category"],
                        "market": asset["market"],
                        "symbol": asset["symbol"],
                        "name": asset["symbol"],
                        "currency": asset["currency"],
                        "price": 100.0,
                        "fx_to_cny": 7.0,
                        "price_cny": 700.0,
                        "source": "test",
                    }
                return quotes, errors

            with patch("crypto_portfolio.price_server.fetch_quotes_for_assets", fake_fetch):
                result = PriceCollector(str(config_path)).fetch_once()

            self.assertEqual(result["status"], "saved")
            self.assertEqual(result["saved_count"], 2)
            history = PriceHistoryStore(str(database)).asset_history(asset_ids=[btc_id, eth_id], compact=False)
            self.assertEqual(set(history["assets"]), {btc_id, eth_id})
            self.assertEqual(len(history["points"]), 1)

    def test_refresh_does_not_save_partial_results_after_retries_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "prices.sqlite3"
            config_path = self.write_config(tmpdir, f"""
[prices]
interval_minutes = 30
database = {database}
fetch_retries = 1
retry_backoff_seconds = 0

[crypto]
enabled = true
symbols = BTC,ETH
""")
            btc_id = asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, "BTC")
            eth_id = asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, "ETH")

            def fake_fetch(assets, max_workers=16):
                quotes = {}
                errors = {}
                for asset in assets:
                    if asset["asset_id"] == eth_id:
                        errors[asset["asset_id"]] = "still failing"
                        continue
                    quotes[asset["asset_id"]] = {
                        "category": asset["category"],
                        "market": asset["market"],
                        "symbol": asset["symbol"],
                        "name": asset["symbol"],
                        "currency": asset["currency"],
                        "price": 100.0,
                        "fx_to_cny": 7.0,
                        "price_cny": 700.0,
                        "source": "test",
                    }
                return quotes, errors

            with patch("crypto_portfolio.price_server.fetch_quotes_for_assets", fake_fetch):
                result = PriceCollector(str(config_path)).fetch_once()

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["saved_count"], 0)
            self.assertEqual(result["missing_assets"], [eth_id])
            history = PriceHistoryStore(str(database)).asset_history(asset_ids=[btc_id, eth_id], compact=False)
            self.assertEqual(history["assets"], {})
            self.assertEqual(history["points"], [])

    def test_refresh_skips_when_all_categories_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self.write_config(tmpdir, """
[prices]
interval_minutes = 30
database = price_history.sqlite3

[crypto]
enabled = false
symbols = BTC

[fund]
enabled = false
codes = 270042

[stock]
enabled = false
us = QQQM
""")

            result = PriceCollector(str(config_path)).fetch_once()
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "no_assets")
            self.assertEqual(result["saved_count"], 0)


if __name__ == "__main__":
    unittest.main()
