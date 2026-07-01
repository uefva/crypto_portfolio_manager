"""Server configuration loading and logging helpers."""

import configparser
from dataclasses import dataclass
from datetime import datetime

from crypto_portfolio.market_data import (
    CATEGORY_CRYPTO,
    CATEGORY_FUND,
    CATEGORY_STOCK,
    MARKET_CRYPTO,
    MARKET_FUND,
    MARKET_HK,
    MARKET_SH,
    MARKET_SZ,
    MARKET_US,
    asset_id_for,
    currency_for,
    normalize_symbol,
)

DEFAULT_CONFIG_PATH = "server_config.ini"
GZIP_MIN_BYTES = 1024
LOG_LEVELS = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
}


@dataclass
class ServerConfig:
    host: str
    port: int
    symbols: list[str]
    assets: list[dict]
    enabled_categories: dict
    asset_counts: dict
    interval_minutes: int
    database: str
    fetch_retries: int
    retry_backoff_seconds: float
    log_level: str


def parse_config_list(value):
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def read_csv(parser, section, option, fallback=""):
    return parse_config_list(parser.get(section, option, fallback=fallback))


def make_config_asset(category, market, symbol):
    symbol = normalize_symbol(symbol, category, market)
    return {
        "asset_id": asset_id_for(category, market, symbol),
        "category": category,
        "market": market,
        "symbol": symbol,
        "name": symbol,
        "currency": currency_for(category, market),
        "quantity": 1.0,
    }


def log_level_value(level):
    return LOG_LEVELS.get(str(level or "INFO").strip().upper(), LOG_LEVELS["INFO"])


def should_log(config, level):
    return log_level_value(level) >= log_level_value(getattr(config, "log_level", "INFO"))


def server_log(config, level, message):
    if not should_log(config, level):
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {level.upper()} {message}", flush=True)


def format_config_counts(config):
    return (
        f"crypto={config.asset_counts.get(CATEGORY_CRYPTO, 0)} "
        f"fund={config.asset_counts.get(CATEGORY_FUND, 0)} "
        f"stock={config.asset_counts.get(CATEGORY_STOCK, 0)}"
    )


def format_enabled_categories(config):
    return (
        f"crypto={bool(config.enabled_categories.get(CATEGORY_CRYPTO))} "
        f"fund={bool(config.enabled_categories.get(CATEGORY_FUND))} "
        f"stock={bool(config.enabled_categories.get(CATEGORY_STOCK))}"
    )


def format_asset_preview(assets, limit=30):
    asset_ids = [asset.get("asset_id", "") for asset in assets]
    preview = asset_ids[:limit]
    suffix = f", ... +{len(asset_ids) - limit}" if len(asset_ids) > limit else ""
    return ",".join(preview) + suffix


def load_config(config_path=DEFAULT_CONFIG_PATH):
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    legacy_symbols = parser.get("prices", "symbols", fallback="BTC,ETH")
    crypto_enabled = parser.getboolean("crypto", "enabled", fallback=True)
    fund_enabled = parser.getboolean("fund", "enabled", fallback=False)
    stock_enabled = parser.getboolean("stock", "enabled", fallback=False)

    crypto_symbols = read_csv(parser, "crypto", "symbols", fallback=legacy_symbols)
    crypto_symbols = [normalize_symbol(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO) for symbol in crypto_symbols]

    fund_codes = read_csv(parser, "fund", "codes", fallback="")
    stock_symbols = {
        MARKET_US: read_csv(parser, "stock", "us", fallback=""),
        MARKET_HK: read_csv(parser, "stock", "hk", fallback=""),
        MARKET_SH: read_csv(parser, "stock", "sh", fallback=""),
        MARKET_SZ: read_csv(parser, "stock", "sz", fallback=""),
    }

    assets = []
    if crypto_enabled:
        assets.extend(
            make_config_asset(CATEGORY_CRYPTO, MARKET_CRYPTO, symbol)
            for symbol in crypto_symbols
        )
    if fund_enabled:
        assets.extend(
            make_config_asset(CATEGORY_FUND, MARKET_FUND, code)
            for code in fund_codes
        )
    if stock_enabled:
        for market, symbols in stock_symbols.items():
            assets.extend(
                make_config_asset(CATEGORY_STOCK, market, symbol)
                for symbol in symbols
            )

    asset_counts = {
        CATEGORY_CRYPTO: sum(1 for asset in assets if asset["category"] == CATEGORY_CRYPTO),
        CATEGORY_FUND: sum(1 for asset in assets if asset["category"] == CATEGORY_FUND),
        CATEGORY_STOCK: sum(1 for asset in assets if asset["category"] == CATEGORY_STOCK),
    }

    return ServerConfig(
        host=parser.get("server", "host", fallback="127.0.0.1"),
        port=parser.getint("server", "port", fallback=8765),
        symbols=crypto_symbols,
        assets=assets,
        enabled_categories={
            CATEGORY_CRYPTO: crypto_enabled,
            CATEGORY_FUND: fund_enabled,
            CATEGORY_STOCK: stock_enabled,
        },
        asset_counts=asset_counts,
        interval_minutes=parser.getint("prices", "interval_minutes", fallback=30),
        database=parser.get("prices", "database", fallback="price_history.sqlite3"),
        fetch_retries=max(parser.getint("prices", "fetch_retries", fallback=3), 0),
        retry_backoff_seconds=max(parser.getfloat("prices", "retry_backoff_seconds", fallback=2.0), 0.0),
        log_level=parser.get("logging", "level", fallback="INFO").strip().upper(),
    )


def should_log_response_payload(config):
    return should_log(config, "DEBUG")
