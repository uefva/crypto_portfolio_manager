"""Compatibility alias for the service-backed portfolio client.

New code should import ``PortfolioApiClient`` from
``crypto_portfolio.portfolio.api_client``.
"""

import sys

from crypto_portfolio.portfolio import api_client as _implementation

sys.modules[__name__] = _implementation
