"""Portfolio import/export behavior for the server store.

The server remains the source of truth, but these helpers keep the explicit JSON
backup/import contract available while old desktop data finishes aging out.
"""

from datetime import datetime
from contextlib import closing
from pathlib import Path
from shutil import copy2
import sqlite3

from crypto_portfolio.market_data import CATEGORY_CRYPTO, MARKET_CRYPTO
from crypto_portfolio.server.utils import now_text


class PortfolioImportExportMixin:
    def export_portfolio(self):
        assets = {
            asset["asset_id"]: {
                "asset_id": asset["asset_id"],
                "category": asset["category"],
                "market": asset["market"],
                "symbol": asset["symbol"],
                "name": asset["name"],
                "currency": asset["currency"],
                "quantity": asset["quantity"],
                "total_cost": asset["total_cost"],
                "total_cost_cny": asset["total_cost_cny"],
                "transactions": [
                    {
                        "id": tx.get("id"),
                        "type": tx.get("type"),
                        "date": tx.get("date"),
                        "amount": tx.get("amount"),
                        "price": tx.get("price"),
                        "total": tx.get("total"),
                        "currency": tx.get("currency", asset["currency"]),
                    }
                    for tx in asset.get("transactions", [])
                ],
            }
            for asset in self.get_assets()
        }
        return {"version": 2, "assets": assets, "exported_at": now_text()}

    def import_portfolio(self, payload):
        raw = payload.get("portfolio", payload) if isinstance(payload, dict) else {}
        if not isinstance(raw, dict):
            raise ValueError("导入内容必须是 JSON 对象。")

        report = {
            "assets_imported": 0,
            "assets_updated": 0,
            "transactions_imported": 0,
            "transactions_skipped": 0,
            "conflicts": [],
            "skipped": [],
            "backup_path": self.backup_local_portfolio_file(),
        }
        if raw.get("version") == 2 and isinstance(raw.get("assets"), dict):
            source_assets = raw["assets"].values()
        else:
            source_assets = []
            for symbol, legacy_asset in raw.items():
                if isinstance(legacy_asset, dict):
                    item = legacy_asset.copy()
                    item.update({
                        "category": CATEGORY_CRYPTO,
                        "market": MARKET_CRYPTO,
                        "symbol": symbol,
                        "name": symbol,
                        "currency": "USD",
                    })
                    source_assets.append(item)

        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            for source in source_assets:
                try:
                    asset = self.normalize_asset_input(
                        source.get("category", CATEGORY_CRYPTO),
                        source.get("market"),
                        source.get("symbol") or source.get("asset_id"),
                        source.get("name", ""),
                    )
                except ValueError as exc:
                    report["skipped"].append({"asset": source, "reason": str(exc)})
                    continue
                existing = conn.execute(
                    "SELECT asset_id FROM portfolio_assets WHERE asset_id = ?",
                    (asset["asset_id"],),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE portfolio_assets SET name = ?, updated_at = ? WHERE asset_id = ?",
                        (asset["name"], now_text(), asset["asset_id"]),
                    )
                    report["assets_updated"] += 1
                else:
                    timestamp = now_text()
                    conn.execute("""
                        INSERT INTO portfolio_assets(
                            asset_id, category, market, symbol, name, currency, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        asset["asset_id"], asset["category"], asset["market"], asset["symbol"],
                        asset["name"], asset["currency"], timestamp, timestamp,
                    ))
                    report["assets_imported"] += 1

                for tx in source.get("transactions", []):
                    if not isinstance(tx, dict):
                        report["transactions_skipped"] += 1
                        continue
                    try:
                        tx_type, amount, price, date, total = self.validate_transaction_payload(tx)
                    except ValueError as exc:
                        report["skipped"].append({"asset_id": asset["asset_id"], "transaction": tx, "reason": str(exc)})
                        report["transactions_skipped"] += 1
                        continue
                    duplicate = conn.execute("""
                        SELECT 1
                        FROM portfolio_transactions
                        WHERE asset_id = ? AND type = ? AND date = ?
                          AND ABS(amount - ?) < 0.000000001
                          AND ABS(price - ?) < 0.000000001
                        LIMIT 1
                    """, (asset["asset_id"], tx_type, date, amount, price)).fetchone()
                    if duplicate:
                        report["transactions_skipped"] += 1
                        continue
                    timestamp = now_text()
                    conn.execute("""
                        INSERT INTO portfolio_transactions(
                            asset_id, type, date, amount, price, total, currency, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        asset["asset_id"], tx_type, date, amount, price, total,
                        asset["currency"], timestamp, timestamp,
                    ))
                    report["transactions_imported"] += 1
            conn.commit()
        return report

    def backup_local_portfolio_file(self):
        source = Path("portfolio.json")
        if not source.exists():
            return ""
        backup_dir = Path("portfolio_backups")
        backup_dir.mkdir(exist_ok=True)
        backup_path = backup_dir / f"portfolio_server_import_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
        copy2(source, backup_path)
        return str(backup_path)
