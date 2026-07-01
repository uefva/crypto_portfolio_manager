"""HTTP handler factory and query parsers for the service API.

The routes preserve the existing price API and add the portfolio API without
changing response shapes used by current clients.
"""

from crypto_portfolio.server.main import (  # noqa: F401
    GZIP_MIN_BYTES,
    make_handler,
    parse_asset_ids,
    parse_bool,
    parse_categories,
    parse_csv,
    parse_limit,
    parse_symbols,
)
