"""Compatibility alias for the desktop Tk application.

New code should import the desktop app from ``crypto_portfolio.desktop.app``.
The alias preserves legacy patching/import behavior for ``crypto_portfolio.gui``.
"""

import importlib
import sys

_implementation = importlib.import_module("crypto_portfolio.desktop.app")

sys.modules[__name__] = _implementation
