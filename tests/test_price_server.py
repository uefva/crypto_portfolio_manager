import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.market_data import CATEGORY_STOCK, MARKET_US, asset_id_for
from crypto_portfolio.price_server import PriceHistoryStore


class PriceHistoryStoreTest(unittest.TestCase):
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

            history = store.asset_history(asset_ids=[asset_id])
            self.assertEqual(history["assets"][asset_id]["symbol"], "QQQM")
            self.assertEqual(history["points"][0]["price_cny"][asset_id], 710.0)


if __name__ == "__main__":
    unittest.main()
