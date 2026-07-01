"""Server runtime entry point."""

from http.server import ThreadingHTTPServer

from crypto_portfolio.market_data import fetch_fx_to_cny, fetch_quotes_for_assets
from crypto_portfolio.server.collector import PriceCollector
from crypto_portfolio.server.config import (
    DEFAULT_CONFIG_PATH,
    format_asset_preview,
    format_config_counts,
    format_enabled_categories,
    load_config,
    server_log,
)
from crypto_portfolio.server.http_api import (
    GZIP_MIN_BYTES,
    make_handler,
    parse_asset_ids,
    parse_bool,
    parse_categories,
    parse_csv,
    parse_limit,
    parse_symbols,
)
from crypto_portfolio.server.portfolio_store import PortfolioStore
from crypto_portfolio.server.price_store import PriceHistoryStore
from crypto_portfolio.server.config import ServerConfig


def run_server(config_path=DEFAULT_CONFIG_PATH):
    config = load_config(config_path)
    PriceHistoryStore(config.database)
    PortfolioStore(config.database)
    collector = PriceCollector(config_path)
    collector.start()

    server = ThreadingHTTPServer(
        (config.host, config.port),
        make_handler(config_path, collector),
    )
    print(f"价格服务已启动: http://{config.host}:{config.port}")
    print(f"配置文件: {config_path}")
    print(f"日志等级: {config.log_level}")
    server_log(
        config,
        "INFO",
        (
            "CONFIG loaded "
            f"path={config_path} "
            f"database={config.database} "
            f"interval_minutes={config.interval_minutes} "
            f"{format_config_counts(config)} "
            f"{format_enabled_categories(config)} "
            f"ids={format_asset_preview(config.assets)}"
        ),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("正在停止价格服务...")
    finally:
        collector.stop()
        server.server_close()


def main():
    run_server()
