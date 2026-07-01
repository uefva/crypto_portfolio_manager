"""Asset category, market, currency, and labeling helpers.

These names are re-exported from the market facade for now so the structural
split does not change behavior. Future changes can move the implementations
here without breaking callers that already adopted the domain path.
"""

from crypto_portfolio.market.facade import (  # noqa: F401
    CATEGORIES,
    CATEGORY_ALL,
    CATEGORY_CRYPTO,
    CATEGORY_FUND,
    CATEGORY_STOCK,
    COIN_MAP,
    MARKET_CRYPTO,
    MARKET_FUND,
    MARKET_HK,
    MARKET_LABELS,
    MARKET_SH,
    MARKET_SZ,
    MARKET_US,
    STOCK_MARKETS,
    asset_id_for,
    asset_label,
    category_code,
    currency_for,
    default_market_for_category,
    normalize_category,
    normalize_market,
    normalize_symbol,
    suggestion_label,
)
