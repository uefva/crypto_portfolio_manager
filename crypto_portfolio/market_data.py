"""Compatibility alias for market data helpers.

New code should import from ``crypto_portfolio.market`` modules. This module is
an alias, not a copy, so existing monkeypatches against the legacy path still
patch the real implementation.
"""

import sys

from crypto_portfolio.market import facade as _implementation

sys.modules[__name__] = _implementation
