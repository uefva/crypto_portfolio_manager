import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.gui import DEFAULT_SERVER_URL, load_gui_server_url


class GuiConfigTest(unittest.TestCase):
    def test_load_gui_server_url_reads_config_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "gui_config.ini"
            config_path.write_text(
                "[server]\nurl = http://example.com:8765\n",
                encoding="utf-8",
            )

            self.assertEqual(load_gui_server_url(str(config_path)), "http://example.com:8765")

    def test_load_gui_server_url_uses_default_when_missing_or_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "missing.ini"
            self.assertEqual(load_gui_server_url(str(missing_path)), DEFAULT_SERVER_URL)

            empty_path = Path(tmpdir) / "empty.ini"
            empty_path.write_text("[server]\nurl = \n", encoding="utf-8")
            self.assertEqual(load_gui_server_url(str(empty_path)), DEFAULT_SERVER_URL)


if __name__ == "__main__":
    unittest.main()
