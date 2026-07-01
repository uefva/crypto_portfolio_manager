import unittest
from unittest.mock import Mock, patch

from crypto_portfolio.market_data import fetch_fund_quote, parse_fund_table_rows


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

        with patch("crypto_portfolio.market_data.request_get", return_value=response):
            quote = fetch_fund_quote("270042")

        self.assertEqual(quote["price"], 1.2345)
        self.assertEqual(quote["price_date"], "2026-06-29")
        self.assertEqual(quote["previous_close"], 1.2)
        self.assertAlmostEqual(quote["change"], 0.0345)
        self.assertAlmostEqual(quote["change_pct"], 0.0345 / 1.2 * 100)


if __name__ == "__main__":
    unittest.main()
