"""Compatibility alias for the local portfolio manager.

New code should import ``PortfolioManager`` from
``crypto_portfolio.portfolio.local_store``. This module aliases the
implementation so legacy monkeypatches still affect the real store.
"""

import sys

from crypto_portfolio.portfolio import local_store as _implementation

sys.modules[__name__] = _implementation
