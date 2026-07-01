import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import crypto_portfolio.portfolio_manager as pm
from crypto_portfolio.market_data import (
    CATEGORY_CRYPTO,
    CATEGORY_FUND,
    CATEGORY_STOCK,
    MARKET_CRYPTO,
    MARKET_FUND,
    MARKET_HK,
    MARKET_US,
    asset_id_for,
)


class PortfolioManagerTest(unittest.TestCase):
    def isolated_manager(self, tmpdir):
        patches = [
            patch.object(pm, "DATA_FILE", str(Path(tmpdir) / "portfolio.json")),
            patch.object(pm, "BACKUP_DIR", str(Path(tmpdir) / "backups")),
            patch.object(pm, "HOLDING_SNAPSHOT_DIR", str(Path(tmpdir) / "snapshots")),
            patch.object(pm, "fetch_fx_to_cny", self.fake_fx),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return pm.PortfolioManager()

    def fake_fx(self, currency, allow_default=True):
        rates = {"CNY": 1.0, "USD": 7.0, "HKD": 0.9}
        return rates[currency], "test-rate", False

    def test_legacy_crypto_data_is_migrated_to_v2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = Path(tmpdir) / "portfolio.json"
            data_path.write_text(json.dumps({
                "BTC": {
                    "quantity": 1.5,
                    "total_cost": 30000,
                    "transactions": [
                        {
                            "type": "buy",
                            "date": "2026-01-01 00:00:00",
                            "amount": 1.5,
                            "price": 20000,
                            "total": 30000,
                        }
                    ],
                }
            }), encoding="utf-8")

            manager = self.isolated_manager(tmpdir)
            asset_id = asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, "BTC")

            self.assertIn(asset_id, manager.data)
            self.assertEqual(manager.data[asset_id]["category"], CATEGORY_CRYPTO)
            self.assertEqual(manager.data[asset_id]["currency"], "USD")
            self.assertEqual(manager.data[asset_id]["total_cost_cny"], 30000)
            self.assertTrue(manager.data[asset_id]["transactions"][0]["migrated_fx"])

            saved = json.loads(data_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["version"], 2)
            self.assertIn(asset_id, saved["assets"])

    def test_buy_sell_tracks_native_and_cny_cost_basis(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self.isolated_manager(tmpdir)
            manager.buy_asset(CATEGORY_STOCK, MARKET_US, "QQQM", 2, 10, "2026-01-01", "QQQM", 7)
            asset_id = asset_id_for(CATEGORY_STOCK, MARKET_US, "QQQM")
            manager.sell_asset(asset_id, 1, 12, "2026-01-02", 7.2)

            asset = manager.data[asset_id]
            self.assertEqual(asset["quantity"], 1)
            self.assertEqual(asset["total_cost"], 10)
            self.assertEqual(asset["total_cost_cny"], 10)
            self.assertEqual(asset["transactions"][1]["total_cny"], 12)

            snapshot = manager.build_holdings_snapshot({
                asset_id: {
                    "price": 11,
                    "currency": "USD",
                    "fx_to_cny": 7.1,
                    "name": "QQQM",
                }
            })
            self.assertAlmostEqual(snapshot["total_value"], 78.1)
            self.assertAlmostEqual(snapshot["total_profit"], 7.1)

    def test_snapshot_contains_category_totals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self.isolated_manager(tmpdir)
            manager.buy_asset(CATEGORY_FUND, MARKET_FUND, "270042", 100, 1.2, "2026-01-01", "基金A", 1)
            manager.buy_asset(CATEGORY_STOCK, MARKET_HK, "700", 10, 20, "2026-01-01", "腾讯", 0.9)

            fund_id = asset_id_for(CATEGORY_FUND, MARKET_FUND, "270042")
            stock_id = asset_id_for(CATEGORY_STOCK, MARKET_HK, "00700")
            snapshot = manager.build_holdings_snapshot({
                fund_id: {
                    "price": 1.3,
                    "currency": "CNY",
                    "fx_to_cny": 1,
                    "name": "基金A",
                },
                stock_id: {
                    "price": 22,
                    "currency": "HKD",
                    "fx_to_cny": 0.9,
                    "name": "腾讯",
                },
            })

            self.assertAlmostEqual(snapshot["category_totals"][CATEGORY_FUND]["total_profit"], 10)
            self.assertAlmostEqual(snapshot["category_totals"][CATEGORY_STOCK]["total_profit"], 18)
            self.assertAlmostEqual(snapshot["total_profit"], 28)

    def test_empty_asset_catalog_entry_is_saved_and_not_counted_as_holding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self.isolated_manager(tmpdir)
            asset = manager.upsert_asset(CATEGORY_FUND, MARKET_FUND, "270042", "基金A")
            asset_id = asset_id_for(CATEGORY_FUND, MARKET_FUND, "270042")

            self.assertEqual(asset["name"], "基金A")
            self.assertIn(asset_id, manager.data)
            self.assertEqual(manager.get_active_assets(), [])

            snapshot = manager.build_holdings_snapshot({})
            self.assertEqual(snapshot["total_value"], 0)
            self.assertTrue(manager.delete_asset(asset_id))
            self.assertNotIn(asset_id, manager.data)

    def test_asset_with_transactions_can_rename_but_not_delete_or_change_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self.isolated_manager(tmpdir)
            manager.buy_asset(CATEGORY_FUND, MARKET_FUND, "270042", 100, 1.2, "2026-01-01", "基金A")
            asset_id = asset_id_for(CATEGORY_FUND, MARKET_FUND, "270042")

            self.assertTrue(manager.update_asset(asset_id, CATEGORY_FUND, MARKET_FUND, "270042", "基金B"))
            self.assertEqual(manager.data[asset_id]["name"], "基金B")
            self.assertFalse(manager.update_asset(asset_id, CATEGORY_FUND, MARKET_FUND, "000001", "基金C"))
            self.assertFalse(manager.delete_asset(asset_id))

    def test_new_orders_do_not_fetch_fx_and_snapshot_uses_current_fx(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = Path(tmpdir) / "portfolio.json"
            backup_dir = Path(tmpdir) / "backups"
            snapshot_dir = Path(tmpdir) / "snapshots"
            patches = [
                patch.object(pm, "DATA_FILE", str(data_path)),
                patch.object(pm, "BACKUP_DIR", str(backup_dir)),
                patch.object(pm, "HOLDING_SNAPSHOT_DIR", str(snapshot_dir)),
                patch.object(pm, "fetch_fx_to_cny", side_effect=AssertionError("fx should not be fetched")),
            ]
            for item in patches:
                item.start()
                self.addCleanup(item.stop)

            manager = pm.PortfolioManager()
            manager.buy_asset(CATEGORY_STOCK, MARKET_US, "QQQM", 2, 10, "2026-01-01", "QQQM")
            asset_id = asset_id_for(CATEGORY_STOCK, MARKET_US, "QQQM")

            snapshot = manager.build_holdings_snapshot({
                asset_id: {
                    "price": 11,
                    "currency": "USD",
                    "fx_to_cny": 7.1,
                    "name": "QQQM",
                }
            })

            self.assertAlmostEqual(snapshot["total_cost"], 142)
            self.assertAlmostEqual(snapshot["total_value"], 156.2)


if __name__ == "__main__":
    unittest.main()
