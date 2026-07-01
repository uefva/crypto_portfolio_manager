"""Desktop configuration helpers.

The desktop client prefers the local price server. Keeping this logic outside
the Tkinter window makes future Web/App clients easier to compare against.
"""

import configparser


DEFAULT_GUI_CONFIG_PATH = "gui_config.ini"
DEFAULT_SERVER_URL = "http://127.0.0.1:8765"


def load_gui_server_url(config_path=DEFAULT_GUI_CONFIG_PATH):
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    url = parser.get("server", "url", fallback=DEFAULT_SERVER_URL).strip()
    return url or DEFAULT_SERVER_URL
