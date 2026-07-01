import importlib
import unittest


class StructureTest(unittest.TestCase):
    def test_legacy_paths_alias_new_implementations(self):
        legacy_market = importlib.import_module("crypto_portfolio.market_data")
        new_market = importlib.import_module("crypto_portfolio.market.facade")
        self.assertIs(legacy_market, new_market)

        legacy_server = importlib.import_module("crypto_portfolio.price_server")
        new_server = importlib.import_module("crypto_portfolio.server.main")
        self.assertIs(legacy_server, new_server)

        legacy_gui = importlib.import_module("crypto_portfolio.gui")
        new_gui = importlib.import_module("crypto_portfolio.desktop.app")
        self.assertIs(legacy_gui, new_gui)

    def test_new_module_paths_expose_core_symbols(self):
        from crypto_portfolio.domain.assets import asset_id_for
        from crypto_portfolio.market.funds import fetch_fund_quote
        from crypto_portfolio.market.stocks import fetch_stock_quote
        from crypto_portfolio.market.crypto import fetch_crypto_quote
        from crypto_portfolio.market.fx import fetch_fx_to_cny
        from crypto_portfolio.portfolio.api_client import PortfolioApiClient
        from crypto_portfolio.portfolio.local_store import PortfolioManager
        from crypto_portfolio.server.price_store import PriceHistoryStore
        from crypto_portfolio.server.portfolio_store import PortfolioStore
        from crypto_portfolio.desktop.config import load_gui_server_url
        from crypto_portfolio.cli import main

        self.assertEqual(asset_id_for("基金", "CN_FUND", "270042"), "fund:CN_FUND:270042")
        self.assertTrue(callable(fetch_fund_quote))
        self.assertTrue(callable(fetch_stock_quote))
        self.assertTrue(callable(fetch_crypto_quote))
        self.assertTrue(callable(fetch_fx_to_cny))
        self.assertTrue(PortfolioApiClient)
        self.assertTrue(PortfolioManager)
        self.assertTrue(PriceHistoryStore)
        self.assertTrue(PortfolioStore)
        self.assertTrue(callable(load_gui_server_url))
        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
