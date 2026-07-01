"""Compatibility alias for the HTTP server.

New code should import server components from ``crypto_portfolio.server``. This
module aliases the real implementation so legacy monkeypatches still work.
"""

import sys
import importlib

_implementation = importlib.import_module("crypto_portfolio.server.main")

sys.modules[__name__] = _implementation
