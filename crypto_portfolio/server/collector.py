"""Background price collection job."""

import threading
import time
from datetime import datetime

from crypto_portfolio.market_data import fetch_quotes_for_assets
from crypto_portfolio.server.config import (
    DEFAULT_CONFIG_PATH,
    format_asset_preview,
    format_config_counts,
    format_enabled_categories,
    load_config,
    server_log,
)
from crypto_portfolio.server.price_store import PriceHistoryStore


class PriceCollector:
    def __init__(self, config_path=DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.stop_event = threading.Event()

    def collect_assets(self, config):
        return list(config.assets)

    def fetch_assets_with_retries(self, assets, config):
        quotes = {}
        errors = {}
        pending_assets = list(assets)
        max_attempts = 1 + max(config.fetch_retries, 0)

        for attempt in range(1, max_attempts + 1):
            # Keep the legacy ``crypto_portfolio.price_server`` patch point
            # working while the implementation lives in this smaller module.
            from crypto_portfolio import price_server as legacy_server

            quote_fetcher = getattr(legacy_server, "fetch_quotes_for_assets", fetch_quotes_for_assets)
            attempt_quotes, attempt_errors = quote_fetcher(pending_assets, max_workers=24)
            pending_ids = {
                asset["asset_id"]
                for asset in pending_assets
            }

            for asset_id, quote in attempt_quotes.items():
                quotes[asset_id] = quote

            missing_ids = pending_ids - set(attempt_quotes)
            errors = {
                asset_id: attempt_errors.get(asset_id, "missing quote")
                for asset_id in missing_ids
            }
            pending_assets = [
                asset for asset in pending_assets
                if asset["asset_id"] in errors
            ]

            if not pending_assets:
                return quotes, {}

            server_log(
                config,
                "WARNING",
                (
                    "COLLECT retry "
                    f"attempt={attempt}/{max_attempts} "
                    f"missing={len(pending_assets)} "
                    f"ids={format_asset_preview(pending_assets)}"
                ),
            )
            if attempt < max_attempts and config.retry_backoff_seconds > 0:
                time.sleep(config.retry_backoff_seconds)

        return quotes, errors

    def fetch_once(self):
        config = load_config(self.config_path)
        assets = self.collect_assets(config)
        fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        assets_by_id = {asset["asset_id"]: asset for asset in assets}
        server_log(
            config,
            "INFO",
            (
                "COLLECT start "
                f"assets={len(assets)} "
                f"{format_config_counts(config)} "
                f"{format_enabled_categories(config)} "
                f"ids={format_asset_preview(assets)}"
            ),
        )
        if not assets:
            server_log(
                config,
                "WARNING",
                (
                    "COLLECT skipped reason=no_assets "
                    f"{format_config_counts(config)} "
                    f"{format_enabled_categories(config)}"
                ),
            )
            return {
                "status": "skipped",
                "reason": "no_assets",
                "fetched_at": fetched_at,
                "saved_count": 0,
                "missing_assets": [],
                "prices": {},
                "assets": {},
            }

        quotes, errors = self.fetch_assets_with_retries(assets, config)
        for asset_id, error in sorted(errors.items()):
            asset = assets_by_id.get(asset_id, {})
            server_log(
                config,
                "WARNING",
                (
                    "COLLECT failed "
                    f"asset_id={asset_id} "
                    f"category={asset.get('category', '')} "
                    f"market={asset.get('market', '')} "
                    f"symbol={asset.get('symbol', '')} "
                    f"error={error}"
                ),
            )
        for asset_id, quote in sorted(quotes.items()):
            server_log(
                config,
                "DEBUG",
                (
                    "COLLECT quote "
                    f"asset_id={asset_id} "
                    f"price={quote.get('price')} "
                    f"currency={quote.get('currency')} "
                    f"price_cny={quote.get('price_cny')} "
                    f"source={quote.get('source')}"
                ),
            )

        store = PriceHistoryStore(config.database)
        if errors or len(quotes) != len(assets):
            missing_assets = sorted(errors or (set(assets_by_id) - set(quotes)))
            server_log(
                config,
                "ERROR",
                (
                    "COLLECT finish "
                    f"status=failed "
                    f"saved=0/{len(assets)} "
                    f"missing={len(missing_assets)} "
                    f"database={config.database}"
                ),
            )
            return {
                "status": "failed",
                "reason": "missing_assets",
                "fetched_at": fetched_at,
                "saved_count": 0,
                "missing_assets": missing_assets,
                "errors": errors,
                "prices": {
                    asset_id: quote["price"]
                    for asset_id, quote in quotes.items()
                },
                "assets": {
                    asset_id: {
                        "category": quote.get("category"),
                        "market": quote.get("market"),
                        "symbol": quote.get("symbol"),
                        "name": quote.get("name"),
                        "currency": quote.get("currency"),
                        "price_cny": quote.get("price_cny"),
                    }
                    for asset_id, quote in quotes.items()
                },
            }

        saved_count = store.save_asset_quotes(quotes, assets_by_id, fetched_at)

        status = "saved" if saved_count == len(assets) else "partial"
        if saved_count == 0:
            status = "skipped"

        server_log(
            config,
            "INFO",
            (
                "COLLECT finish "
                f"status={status} "
                f"saved={saved_count}/{len(assets)} "
                f"missing={len(errors)} "
                f"database={config.database}"
            ),
        )

        return {
            "status": status,
            "fetched_at": fetched_at,
            "saved_count": saved_count,
            "missing_assets": sorted(errors),
            "errors": errors,
            "prices": {
                asset_id: quote["price"]
                for asset_id, quote in quotes.items()
            },
            "assets": {
                asset_id: {
                    "category": quote.get("category"),
                    "market": quote.get("market"),
                    "symbol": quote.get("symbol"),
                    "name": quote.get("name"),
                    "currency": quote.get("currency"),
                    "price_cny": quote.get("price_cny"),
                }
                for asset_id, quote in quotes.items()
            },
        }

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        return thread

    def stop(self):
        self.stop_event.set()

    def run(self):
        while not self.stop_event.is_set():
            try:
                result = self.fetch_once()
                missing = result.get("missing_assets", [])
                missing_text = f"，缺失 {len(missing)} 个资产" if missing else ""
                print(f"{result['fetched_at']} 价格采集 {result['status']}，保存 {result['saved_count']} 条{missing_text}。")
            except Exception as exc:
                print(f"价格采集失败: {exc}")

            config = load_config(self.config_path)
            interval_seconds = max(config.interval_minutes, 1) * 60
            self.stop_event.wait(interval_seconds)


