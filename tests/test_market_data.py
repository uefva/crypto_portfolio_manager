import unittest
from unittest.mock import Mock, patch

from crypto_portfolio.market_data import (
    CATEGORY_FUND,
    CATEGORY_STOCK,
    MARKET_HK,
    MARKET_US,
    fetch_fund_quote,
    parse_fund_catalog,
    parse_fund_table_rows,
    search_fund_suggestions,
    search_stock_suggestions,
)


class MarketDataTest(unittest.TestCase):
    def test_parse_fund_table_rows_extracts_latest_and_previous_values(self):
        table_html = """
        <table>
          <tr><th>净值日期</th><th>单位净值</th><th>累计净值</th></tr>
          <tr><td>2026-06-29</td><td>1.2345</td><td>2.0000</td></tr>
          <tr><td>2026-06-28</td><td>1.2000</td><td>1.9900</td></tr>
        </table>
        """

        rows = parse_fund_table_rows(table_html, "270042")

        self.assertEqual(rows[0]["date"], "2026-06-29")
        self.assertEqual(rows[0]["unit_value"], 1.2345)
        self.assertEqual(rows[1]["unit_value"], 1.2)

    def test_fetch_fund_quote_uses_standard_library_parser(self):
        response = Mock()
        response.text = (
            'var apidata={ content:"'
            '<table><tr><th>净值日期</th><th>单位净值</th></tr>'
            '<tr><td>2026-06-29</td><td>1.2345</td></tr>'
            '<tr><td>2026-06-28</td><td>1.2000</td></tr>'
            '</table>",records:2};'
        )

        with (
            patch("crypto_portfolio.market_data.request_get", return_value=response),
            patch("crypto_portfolio.market_data.fund_name_for_code", return_value="基金A"),
        ):
            quote = fetch_fund_quote("270042")

        self.assertEqual(quote["price"], 1.2345)
        self.assertEqual(quote["name"], "基金A")
        self.assertEqual(quote["price_date"], "2026-06-29")
        self.assertEqual(quote["previous_close"], 1.2)
        self.assertAlmostEqual(quote["change"], 0.0345)
        self.assertAlmostEqual(quote["change_pct"], 0.0345 / 1.2 * 100)

    def test_parse_fund_catalog_and_search_by_code_name_or_pinyin(self):
        catalog_text = (
            'var r = [["270042","GAFXLH","广发消费领先混合A",'
            '"混合型-偏股","GUANGFAXIAOFEILINGXIANHUNHEA"]];'
        )
        catalog = parse_fund_catalog(catalog_text)

        self.assertEqual(catalog[0]["category"], CATEGORY_FUND)
        self.assertEqual(catalog[0]["symbol"], "270042")
        self.assertEqual(catalog[0]["name"], "广发消费领先混合A")

        with patch("crypto_portfolio.market_data.fetch_fund_catalog", return_value=catalog):
            self.assertEqual(search_fund_suggestions("消费")[0]["symbol"], "270042")
            self.assertEqual(search_fund_suggestions("GAF")[0]["symbol"], "270042")

    def test_search_stock_suggestions_maps_markets(self):
        response = Mock()
        response.json.return_value = {
            "QuotationCodeTable": {
                "Data": [
                    {
                        "Code": "QQQ",
                        "Name": "纳斯达克100ETF-Invesco",
                        "PinYin": "NSDK100ETFINVESCO",
                        "MktNum": "105",
                        "Classify": "UsStock",
                        "SecurityTypeName": "美股",
                    },
                    {
                        "Code": "00700",
                        "Name": "腾讯控股",
                        "PinYin": "TXKG",
                        "MktNum": "116",
                        "Classify": "HK",
                        "SecurityTypeName": "港股",
                    },
                ]
            }
        }

        with patch("crypto_portfolio.market_data.request_get", return_value=response):
            us_matches = search_stock_suggestions("QQQ", market=MARKET_US)
            hk_matches = search_stock_suggestions("腾讯", market=MARKET_HK)

        self.assertEqual(us_matches[0]["category"], CATEGORY_STOCK)
        self.assertEqual(us_matches[0]["symbol"], "QQQ")
        self.assertEqual(us_matches[0]["market"], MARKET_US)
        self.assertEqual(hk_matches[0]["symbol"], "00700")
        self.assertEqual(hk_matches[0]["market"], MARKET_HK)


if __name__ == "__main__":
    unittest.main()
