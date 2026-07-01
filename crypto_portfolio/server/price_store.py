"""SQLite price-history storage."""

import sqlite3
from contextlib import closing
from pathlib import Path

from crypto_portfolio.market_data import (
    CATEGORY_CRYPTO,
    MARKET_CRYPTO,
    asset_id_for,
    fetch_fx_to_cny,
    normalize_symbol,
)


class PriceHistoryStore:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        if self.database_path.parent != Path("."):
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self):
        return sqlite3.connect(self.database_path)

    def init_schema(self):
        with closing(self.connect()) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS asset_price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    price REAL NOT NULL,
                    fx_to_cny REAL NOT NULL,
                    price_cny REAL NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    UNIQUE(asset_id, fetched_at)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_asset_price_history_asset_time
                ON asset_price_history(asset_id, fetched_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_asset_price_history_category_time
                ON asset_price_history(category, fetched_at)
            """)
            self.migrate_legacy_price_history(conn)
            conn.commit()

    def legacy_price_history_exists(self, conn):
        row = conn.execute("""
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'price_history'
            LIMIT 1
        """).fetchone()
        return row is not None

    def migrate_legacy_price_history(self, conn):
        if not self.legacy_price_history_exists(conn):
            return 0

        row = conn.execute("""
            SELECT 1
            FROM price_history ph
            LEFT JOIN asset_price_history aph
              ON aph.asset_id = 'crypto:CRYPTO:' || UPPER(ph.symbol)
             AND aph.fetched_at = ph.fetched_at
            WHERE aph.id IS NULL
            LIMIT 1
        """).fetchone()
        if row is None:
            return 0

        # Tests and older callers patch ``crypto_portfolio.price_server``.
        # Resolve through that compatibility module so the split remains transparent.
        from crypto_portfolio import price_server as legacy_server

        fx_fetcher = getattr(legacy_server, "fetch_fx_to_cny", fetch_fx_to_cny)
        fx_to_cny, _fx_source, _fx_estimated = fx_fetcher("USD")
        rows = []
        for row in conn.execute("""
            SELECT symbol, price, source, fetched_at
            FROM price_history
            WHERE symbol IS NOT NULL AND price IS NOT NULL AND fetched_at IS NOT NULL
        """):
            symbol = normalize_symbol(row[0], CATEGORY_CRYPTO, MARKET_CRYPTO)
            asset_id = asset_id_for(CATEGORY_CRYPTO, MARKET_CRYPTO, symbol)
            price = float(row[1])
            rows.append((
                asset_id,
                CATEGORY_CRYPTO,
                MARKET_CRYPTO,
                symbol,
                symbol,
                "USD",
                price,
                float(fx_to_cny),
                price * float(fx_to_cny),
                row[2] or "legacy",
                row[3],
            ))

        if not rows:
            return 0

        conn.executemany("""
            INSERT OR IGNORE INTO asset_price_history(
                asset_id, category, market, symbol, name, currency,
                price, fx_to_cny, price_cny, source, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        return len(rows)

    def save_asset_quotes(self, quotes, assets_by_id, fetched_at):
        if not quotes:
            return 0

        rows = []
        for asset_id, quote in quotes.items():
            asset = assets_by_id.get(asset_id, {})
            rows.append((
                asset_id,
                quote.get("category") or asset.get("category", ""),
                quote.get("market") or asset.get("market", ""),
                quote.get("symbol") or asset.get("symbol", ""),
                quote.get("name") or asset.get("name") or quote.get("symbol") or "",
                quote.get("currency") or asset.get("currency", ""),
                float(quote["price"]),
                float(quote.get("fx_to_cny", 1.0)),
                float(quote.get("price_cny", quote["price"])),
                quote.get("source", "unknown"),
                fetched_at,
            ))

        with closing(self.connect()) as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO asset_price_history(
                    asset_id, category, market, symbol, name, currency,
                    price, fx_to_cny, price_cny, source, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            conn.commit()
        return len(rows)

    def latest_prices(self, symbols):
        if not symbols:
            return {}

        latest = {}
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            for symbol in symbols:
                symbol = normalize_symbol(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO)
                row = conn.execute("""
                    SELECT symbol, price, source, fetched_at
                    FROM asset_price_history
                    WHERE category = ? AND market = ? AND symbol = ?
                    ORDER BY fetched_at DESC
                    LIMIT 1
                """, (CATEGORY_CRYPTO, MARKET_CRYPTO, symbol)).fetchone()
                if row:
                    latest[symbol] = {
                        "price": row["price"],
                        "source": row["source"],
                        "fetched_at": row["fetched_at"],
                    }
        return latest

    def latest_asset_prices(self, asset_ids=None, categories=None):
        filters, params = self.asset_filters(asset_ids, categories)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        query = f"""
            SELECT aph.*
            FROM asset_price_history aph
            JOIN (
                SELECT asset_id, MAX(fetched_at) AS latest_at
                FROM asset_price_history
                {where}
                GROUP BY asset_id
            ) latest
            ON aph.asset_id = latest.asset_id AND aph.fetched_at = latest.latest_at
            ORDER BY aph.category ASC, aph.market ASC, aph.symbol ASC
        """
        latest = {}
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(query, params):
                latest[row["asset_id"]] = self.asset_row_to_payload(row)
        return latest

    def history(self, symbols, start=None, end=None, limit=2000):
        if not symbols:
            return []

        symbols = [normalize_symbol(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO) for symbol in symbols]
        placeholders = ",".join("?" for _ in symbols)
        params = [CATEGORY_CRYPTO, MARKET_CRYPTO, *symbols]
        filters = ["category = ?", "market = ?", f"symbol IN ({placeholders})"]
        if start:
            filters.append("fetched_at >= ?")
            params.append(start)
        if end:
            filters.append("fetched_at <= ?")
            params.append(end)
        params.append(limit)

        query = f"""
            SELECT symbol, price, source, fetched_at
            FROM asset_price_history
            WHERE {' AND '.join(filters)}
            ORDER BY fetched_at ASC, symbol ASC
            LIMIT ?
        """

        points_by_time = {}
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(query, params):
                point = points_by_time.setdefault(
                    row["fetched_at"],
                    {"timestamp": row["fetched_at"], "prices": {}, "sources": {}},
                )
                point["prices"][row["symbol"]] = row["price"]
                point["sources"][row["symbol"]] = row["source"]

        return list(points_by_time.values())

    def asset_history(self, asset_ids=None, categories=None, start=None, end=None, limit=5000, compact=True):
        filters, params = self.asset_filters(asset_ids, categories)
        if start:
            filters.append("fetched_at >= ?")
            params.append(start)
        if end:
            filters.append("fetched_at <= ?")
            params.append(end)

        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        limit_clause = "LIMIT ?" if limit and limit > 0 else ""
        time_params = list(params)
        if limit_clause:
            time_params.append(limit)

        outer_filters, outer_params = self.asset_filters(asset_ids, categories, prefix="aph.")
        outer_where = f"WHERE {' AND '.join(outer_filters)}" if outer_filters else ""
        query_params = time_params + outer_params
        selected_columns = (
            "aph.asset_id, aph.fetched_at, aph.price_cny"
            if compact
            else "aph.*"
        )
        query = f"""
            WITH selected_times AS (
                SELECT DISTINCT fetched_at
                FROM asset_price_history
                {where}
                ORDER BY fetched_at ASC
                {limit_clause}
            )
            SELECT {selected_columns}
            FROM asset_price_history aph
            JOIN selected_times st ON aph.fetched_at = st.fetched_at
            {outer_where}
            ORDER BY aph.fetched_at ASC, aph.asset_id ASC
        """

        assets = {}
        points_by_time = {}
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(query, query_params):
                if not compact:
                    assets[row["asset_id"]] = {
                        "asset_id": row["asset_id"],
                        "category": row["category"],
                        "market": row["market"],
                        "symbol": row["symbol"],
                        "name": row["name"],
                        "currency": row["currency"],
                    }
                point = points_by_time.setdefault(
                    row["fetched_at"],
                    self.empty_asset_history_point(row["fetched_at"], compact),
                )
                point["price_cny"][row["asset_id"]] = row["price_cny"]
                if not compact:
                    point["prices"][row["asset_id"]] = row["price"]
                    point["fx_to_cny"][row["asset_id"]] = row["fx_to_cny"]
                    point["sources"][row["asset_id"]] = row["source"]

        payload = {"points": list(points_by_time.values())}
        if not compact:
            payload["assets"] = assets
        return payload

    def empty_asset_history_point(self, fetched_at, compact=True):
        point = {
            "timestamp": fetched_at,
            "price_cny": {},
        }
        if not compact:
            point.update({
                "prices": {},
                "fx_to_cny": {},
                "sources": {},
            })
        return point

    def asset_filters(self, asset_ids=None, categories=None, prefix=""):
        filters = []
        params = []
        if asset_ids:
            placeholders = ",".join("?" for _ in asset_ids)
            filters.append(f"{prefix}asset_id IN ({placeholders})")
            params.extend(asset_ids)
        if categories:
            placeholders = ",".join("?" for _ in categories)
            filters.append(f"{prefix}category IN ({placeholders})")
            params.extend(categories)
        return filters, params

    def asset_row_to_payload(self, row):
        return {
            "asset_id": row["asset_id"],
            "category": row["category"],
            "market": row["market"],
            "symbol": row["symbol"],
            "name": row["name"],
            "currency": row["currency"],
            "price": row["price"],
            "fx_to_cny": row["fx_to_cny"],
            "price_cny": row["price_cny"],
            "source": row["source"],
            "fetched_at": row["fetched_at"],
        }


