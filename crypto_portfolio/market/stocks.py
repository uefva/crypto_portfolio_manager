"""Stock quote and EastMoney stock suggestion helpers.

EastMoney market identifiers are intentionally hidden behind these functions so
the rest of the app can work with normalized market codes such as ``US``/``HK``.
"""

from crypto_portfolio.market.facade import (  # noqa: F401
    fetch_eastmoney_quote,
    fetch_stock_quote,
    market_from_eastmoney_row,
    search_stock_suggestions,
    stock_secids,
)
