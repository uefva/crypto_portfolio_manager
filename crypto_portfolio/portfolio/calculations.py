"""Portfolio calculation helpers.

The app stores orders in native asset currency and converts to CNY only when a
quote supplies a current or historical FX rate. ``PortfolioManager`` currently
owns the concrete implementation; this module exposes the calculation entry
point for new code.
"""

from crypto_portfolio.portfolio.local_store import safe_float  # noqa: F401
