import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from crypto_portfolio.gui import DEFAULT_SERVER_URL, PortfolioApp, load_gui_server_url


class ValueVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeManager:
    def get_active_assets(self):
        return [{
            "asset_id": "crypto:CRYPTO:BTC",
            "category": "加密货币",
            "market": "CRYPTO",
            "symbol": "BTC",
            "name": "BTC",
            "quantity": 1.0,
            "total_cost": 20.0,
            "total_cost_cny": 20.0,
            "currency": "USD",
        }]


class GuiConfigTest(unittest.TestCase):
    def test_load_gui_server_url_reads_config_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "gui_config.ini"
            config_path.write_text(
                "[server]\nurl = http://example.com:8765\n",
                encoding="utf-8",
            )

            self.assertEqual(load_gui_server_url(str(config_path)), "http://example.com:8765")

    def test_load_gui_server_url_uses_default_when_missing_or_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "missing.ini"
            self.assertEqual(load_gui_server_url(str(missing_path)), DEFAULT_SERVER_URL)

            empty_path = Path(tmpdir) / "empty.ini"
            empty_path.write_text("[server]\nurl = \n", encoding="utf-8")
            self.assertEqual(load_gui_server_url(str(empty_path)), DEFAULT_SERVER_URL)

    def test_server_history_request_uses_unlimited_limit_for_all_time(self):
        app = PortfolioApp.__new__(PortfolioApp)
        app.manager = FakeManager()
        app.chart_metric_var = ValueVar("收益金额")
        app.normalize_server_url = lambda: "http://server.test"
        app.get_chart_range_start = lambda: None
        captured = {}

        def fake_fetch(server_url, path, params):
            captured.update({"server_url": server_url, "path": path, "params": params})
            return {
                "points": [{
                    "timestamp": "2026-01-01 00:00:00",
                    "price_cny": {"crypto:CRYPTO:BTC": 120.0},
                    "fx_to_cny": {"crypto:CRYPTO:BTC": 7.0},
                }]
            }

        app.fetch_server_json = fake_fetch

        result = PortfolioApp.build_server_profit_chart_data(app)

        self.assertEqual(captured["server_url"], "http://server.test")
        self.assertEqual(captured["path"], "/api/assets/history")
        self.assertEqual(captured["params"]["asset_ids"], "crypto:CRYPTO:BTC")
        self.assertEqual(captured["params"]["limit"], "0")
        self.assertEqual(captured["params"]["full"], "1")
        self.assertNotIn("start", captured["params"])
        self.assertEqual(result["labels"], ["2026-01-01 00:00:00"])
        self.assertEqual(result["source"], "server")
        self.assertTrue(result["series"])
        self.assertEqual(result["series"]["总资产"][0][1], -20.0)

    def test_server_history_request_keeps_start_for_limited_ranges(self):
        app = PortfolioApp.__new__(PortfolioApp)
        app.manager = FakeManager()
        app.chart_metric_var = ValueVar("收益金额")
        app.normalize_server_url = lambda: "http://server.test"
        app.get_chart_range_start = lambda: datetime(2026, 1, 1, 0, 0, 0)
        captured = {}

        def fake_fetch(_server_url, _path, params):
            captured.update(params)
            return {"points": []}

        app.fetch_server_json = fake_fetch

        PortfolioApp.build_server_profit_chart_data(app)

        self.assertEqual(captured["limit"], "5000")
        self.assertEqual(captured["start"], "2026-01-01 00:00:00")


if __name__ == "__main__":
    unittest.main()
