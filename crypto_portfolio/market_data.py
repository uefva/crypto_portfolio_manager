import html
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html.parser import HTMLParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


CATEGORY_CRYPTO = "加密货币"
CATEGORY_STOCK = "股票"
CATEGORY_FUND = "基金"
CATEGORY_ALL = "全部"
CATEGORIES = (CATEGORY_FUND, CATEGORY_STOCK, CATEGORY_CRYPTO)

MARKET_CRYPTO = "CRYPTO"
MARKET_FUND = "CN_FUND"
MARKET_SH = "SH"
MARKET_SZ = "SZ"
MARKET_HK = "HK"
MARKET_US = "US"

MARKET_LABELS = {
    MARKET_CRYPTO: "加密货币",
    MARKET_FUND: "国内基金",
    MARKET_SH: "A股-上海",
    MARKET_SZ: "A股-深圳",
    MARKET_HK: "港股",
    MARKET_US: "美股",
}

STOCK_MARKETS = (MARKET_SH, MARKET_SZ, MARKET_HK, MARKET_US)

COIN_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "ADA": "cardano",
    "SOL": "solana",
    "SUI": "sui",
    "PEPE": "pepe",
    "DOGE": "dogecoin",
}

OKX_SYMBOL_MAP = {
    "BTC": "BTC-USDT",
    "ETH": "ETH-USDT",
    "ADA": "ADA-USDT",
    "SOL": "SOL-USDT",
    "SUI": "SUI-USDT",
    "PEPE": "PEPE-USDT",
    "DOGE": "DOGE-USDT",
}

BINANCE_SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "ADA": "ADAUSDT",
    "SOL": "SOLUSDT",
    "SUI": "SUIUSDT",
    "PEPE": "PEPEUSDT",
    "DOGE": "DOGEUSDT",
}

PRICE_TIMEOUT = 6
DEFAULT_FX_TO_CNY = {
    "CNY": 1.0,
    "USD": 7.2,
    "HKD": 0.92,
}
FX_CACHE_SECONDS = 600
_FX_CACHE = {}


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_session(trust_env=False):
    session = requests.Session()
    session.trust_env = trust_env
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Referer": "https://quote.eastmoney.com/",
    })

    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


DIRECT_SESSION = make_session(trust_env=False)
PROXY_SESSION = make_session(trust_env=True)


def request_get(url, params=None, timeout=PRICE_TIMEOUT, allow_proxy_fallback=True):
    try:
        response = DIRECT_SESSION.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response
    except Exception:
        if not allow_proxy_fallback:
            raise

    response = PROXY_SESSION.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response


def to_float(value):
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_category(category):
    text = str(category or CATEGORY_CRYPTO).strip()
    aliases = {
        "crypto": CATEGORY_CRYPTO,
        "coin": CATEGORY_CRYPTO,
        "加密": CATEGORY_CRYPTO,
        "加密货币": CATEGORY_CRYPTO,
        "stock": CATEGORY_STOCK,
        "股票": CATEGORY_STOCK,
        "fund": CATEGORY_FUND,
        "基金": CATEGORY_FUND,
    }
    return aliases.get(text.lower(), aliases.get(text, text))


def default_market_for_category(category):
    category = normalize_category(category)
    if category == CATEGORY_FUND:
        return MARKET_FUND
    if category == CATEGORY_STOCK:
        return MARKET_US
    return MARKET_CRYPTO


def currency_for(category, market):
    category = normalize_category(category)
    market = normalize_market(market, category)
    if category == CATEGORY_FUND:
        return "CNY"
    if category == CATEGORY_CRYPTO:
        return "USD"
    if market in {MARKET_SH, MARKET_SZ}:
        return "CNY"
    if market == MARKET_HK:
        return "HKD"
    return "USD"


def normalize_market(market, category=CATEGORY_CRYPTO):
    category = normalize_category(category)
    text = str(market or default_market_for_category(category)).strip().upper()
    aliases = {
        "CRYPTO": MARKET_CRYPTO,
        "币": MARKET_CRYPTO,
        "CN_FUND": MARKET_FUND,
        "FUND": MARKET_FUND,
        "基金": MARKET_FUND,
        "SH": MARKET_SH,
        "SSE": MARKET_SH,
        "上海": MARKET_SH,
        "A股-上海": MARKET_SH,
        "SZ": MARKET_SZ,
        "SZSE": MARKET_SZ,
        "深圳": MARKET_SZ,
        "A股-深圳": MARKET_SZ,
        "HK": MARKET_HK,
        "HKG": MARKET_HK,
        "港股": MARKET_HK,
        "US": MARKET_US,
        "USA": MARKET_US,
        "美股": MARKET_US,
    }
    return aliases.get(text, aliases.get(str(market or "").strip(), text))


def normalize_symbol(symbol, category=CATEGORY_CRYPTO, market=None):
    category = normalize_category(category)
    market = normalize_market(market, category)
    text = str(symbol or "").strip().upper()
    if market in {MARKET_SH, MARKET_SZ}:
        return text.zfill(6)
    if market == MARKET_HK:
        return text.zfill(5)
    return text


def category_code(category):
    category = normalize_category(category)
    return {
        CATEGORY_CRYPTO: "crypto",
        CATEGORY_STOCK: "stock",
        CATEGORY_FUND: "fund",
    }.get(category, "asset")


def asset_id_for(category, market, symbol):
    category = normalize_category(category)
    market = normalize_market(market, category)
    symbol = normalize_symbol(symbol, category, market)
    return f"{category_code(category)}:{market}:{symbol}"


def asset_label(asset):
    category = asset.get("category", CATEGORY_CRYPTO)
    symbol = asset.get("symbol", "")
    name = asset.get("name") or symbol
    if name and name != symbol:
        return f"{category} {symbol} {name}"
    return f"{category} {symbol}"


def fetch_eastmoney_quote(secids):
    if isinstance(secids, str):
        secids = [secids]
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": 2,
        "fields": "f2,f3,f4,f12,f13,f14,f18",
        "secids": ",".join(secids),
    }
    response = request_get(url, params=params, timeout=PRICE_TIMEOUT)
    data = response.json().get("data", {}).get("diff", [])
    return data or []


def stock_secids(market, symbol):
    market = normalize_market(market, CATEGORY_STOCK)
    symbol = normalize_symbol(symbol, CATEGORY_STOCK, market)
    if market == MARKET_SH:
        return [f"1.{symbol}"]
    if market == MARKET_SZ:
        return [f"0.{symbol}"]
    if market == MARKET_HK:
        return [f"116.{symbol}"]
    if market == MARKET_US:
        # EastMoney splits US securities across several market ids. Query all
        # common ids and use the first row with a valid latest price.
        return [f"105.{symbol}", f"106.{symbol}", f"107.{symbol}"]
    raise ValueError(f"不支持的股票市场: {market}")


def fetch_stock_quote(market, symbol):
    market = normalize_market(market, CATEGORY_STOCK)
    symbol = normalize_symbol(symbol, CATEGORY_STOCK, market)
    for row in fetch_eastmoney_quote(stock_secids(market, symbol)):
        price = to_float(row.get("f2"))
        if price is None or price <= 0:
            continue
        return {
            "price": price,
            "currency": currency_for(CATEGORY_STOCK, market),
            "source": "东方财富",
            "name": row.get("f14") or symbol,
            "fetched_at": now_text(),
            "previous_close": to_float(row.get("f18")),
            "change": to_float(row.get("f4")),
            "change_pct": to_float(row.get("f3")),
        }
    raise ValueError(f"未获取到股票行情: {market} {symbol}")


def extract_eastmoney_fund_table(text, fund_code):
    match = re.search(r'content:"(.*?)",records:', text, re.S)
    if not match:
        raise ValueError(f"{fund_code} 没有找到基金净值表格")

    table_html = match.group(1)
    table_html = table_html.replace('\\"', '"').replace("\\/", "/")
    return html.unescape(table_html)


class FundTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_cell = False
        self.current_cell = []
        self.current_row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"td", "th"}:
            self.in_cell = True
            self.current_cell = []

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self.in_cell:
            text = html.unescape("".join(self.current_cell)).strip()
            self.current_row.append(re.sub(r"\s+", " ", text))
            self.in_cell = False
        elif tag == "tr":
            if self.current_row:
                self.rows.append(self.current_row)
                self.current_row = []


def find_column_index(headers, candidates):
    for candidate in candidates:
        for index, header in enumerate(headers):
            if candidate in header:
                return index
    return None


def parse_fund_table_rows(table_html, fund_code):
    parser = FundTableParser()
    parser.feed(table_html)
    if len(parser.rows) < 2:
        raise ValueError(f"{fund_code} fund net value table is empty")

    headers = [header.replace(" ", "") for header in parser.rows[0]]
    date_index = find_column_index(headers, ("净值日期", "日期"))
    value_index = find_column_index(headers, ("单位净值",))
    if date_index is None or value_index is None:
        raise ValueError(f"{fund_code} fund net value table is missing required columns")

    parsed_rows = []
    for row in parser.rows[1:]:
        if len(row) <= max(date_index, value_index):
            continue
        value = to_float(row[value_index])
        if value is None:
            continue
        parsed_rows.append({
            "date": row[date_index],
            "unit_value": value,
        })

    if not parsed_rows:
        raise ValueError(f"{fund_code} fund net value table has no valid rows")
    return parsed_rows


def fetch_fund_quote(fund_code):
    fund_code = normalize_symbol(fund_code, CATEGORY_FUND, MARKET_FUND)
    url = "http://fund.eastmoney.com/f10/F10DataApi.aspx"
    params = {
        "type": "lsjz",
        "code": fund_code,
        "page": 1,
        "per": 2,
    }
    response = request_get(url, params=params, timeout=PRICE_TIMEOUT)
    response.encoding = "utf-8"
    table_html = extract_eastmoney_fund_table(response.text, fund_code)
    rows = parse_fund_table_rows(table_html, fund_code)

    latest = rows[0]
    previous_close = None
    change = None
    change_pct = None
    if len(rows) >= 2:
        previous = rows[1]["unit_value"]
        current = latest["unit_value"]
        if previous > 0:
            previous_close = previous
            change = current - previous
            change_pct = change / previous * 100

    return {
        "price": latest["unit_value"],
        "currency": "CNY",
        "source": "东方财富基金净值",
        "name": fund_code,
        "fetched_at": now_text(),
        "price_date": latest["date"],
        "previous_close": previous_close,
        "change": change,
        "change_pct": change_pct,
    }


def parse_price(value):
    price = to_float(value)
    if price is None or price <= 0:
        return None
    return price


def fetch_okx_price(symbol):
    inst_id = OKX_SYMBOL_MAP.get(symbol)
    if not inst_id:
        return None
    response = requests.get(
        "https://www.okx.com/api/v5/market/ticker",
        params={"instId": inst_id},
        timeout=PRICE_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json().get("data", [])
    if not data:
        return None
    return parse_price(data[0].get("last"))


def fetch_binance_price(symbol):
    ticker_symbol = BINANCE_SYMBOL_MAP.get(symbol)
    if not ticker_symbol:
        return None
    response = requests.get(
        "https://data-api.binance.vision/api/v3/ticker/price",
        params={"symbol": ticker_symbol},
        timeout=PRICE_TIMEOUT,
    )
    response.raise_for_status()
    return parse_price(response.json().get("price"))


def fetch_coingecko_price(symbol):
    coin_id = COIN_MAP.get(symbol)
    if not coin_id:
        return None
    response = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": coin_id, "vs_currencies": "usd"},
        timeout=PRICE_TIMEOUT,
    )
    response.raise_for_status()
    return parse_price(response.json().get(coin_id, {}).get("usd"))


def fetch_crypto_quote(symbol):
    symbol = normalize_symbol(symbol, CATEGORY_CRYPTO, MARKET_CRYPTO)
    tasks = [
        ("OKX", fetch_okx_price),
        ("Binance", fetch_binance_price),
        ("CoinGecko", fetch_coingecko_price),
    ]
    executor = ThreadPoolExecutor(max_workers=3)
    future_meta = {
        executor.submit(fetch_price, symbol): source
        for source, fetch_price in tasks
    }
    try:
        errors = []
        for future in as_completed(future_meta):
            source = future_meta[future]
            try:
                price = future.result()
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                continue
            if price is not None:
                return {
                    "price": price,
                    "currency": "USD",
                    "source": source,
                    "name": symbol,
                    "fetched_at": now_text(),
                }
        raise ValueError(f"未获取到加密货币价格: {symbol}; {'; '.join(errors)}")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def fetch_fx_from_eastmoney(currency):
    currency = str(currency or "CNY").upper()
    if currency == "CNY":
        return 1.0, "人民币"
    secids = {
        "USD": "133.USDCNH",
        "HKD": "133.HKDCNH",
    }
    secid = secids.get(currency)
    if not secid:
        raise ValueError(f"不支持的汇率币种: {currency}")

    for row in fetch_eastmoney_quote(secid):
        rate = to_float(row.get("f2"))
        if rate is not None and rate > 0:
            return rate, "东方财富汇率"
    raise ValueError(f"未获取到 {currency}/CNY 汇率")


def fetch_fx_from_open_er(currency):
    currency = str(currency or "CNY").upper()
    if currency == "CNY":
        return 1.0, "人民币"
    response = request_get(
        f"https://open.er-api.com/v6/latest/{currency}",
        timeout=PRICE_TIMEOUT,
        allow_proxy_fallback=False,
    )
    data = response.json()
    rate = to_float(data.get("rates", {}).get("CNY"))
    if data.get("result") == "success" and rate is not None and rate > 0:
        return rate, "open.er-api.com"
    raise ValueError(f"备用汇率接口未返回 {currency}/CNY")


def fetch_fx_to_cny(currency, allow_default=True):
    currency = str(currency or "CNY").upper()
    if currency == "CNY":
        return 1.0, "人民币", False

    cached = _FX_CACHE.get(currency)
    if cached and time.time() - cached["cached_at"] <= FX_CACHE_SECONDS:
        return cached["rate"], cached["source"], cached["estimated"]

    errors = []
    for fetcher in (fetch_fx_from_eastmoney, fetch_fx_from_open_er):
        try:
            rate, source = fetcher(currency)
            _FX_CACHE[currency] = {
                "rate": rate,
                "source": source,
                "estimated": False,
                "cached_at": time.time(),
            }
            return rate, source, False
        except Exception as exc:
            errors.append(str(exc))

    if allow_default and currency in DEFAULT_FX_TO_CNY:
        rate = DEFAULT_FX_TO_CNY[currency]
        source = f"默认估算汇率 ({'; '.join(errors)})"
        _FX_CACHE[currency] = {
            "rate": rate,
            "source": source,
            "estimated": True,
            "cached_at": time.time(),
        }
        return rate, source, True

    raise ValueError(f"无法获取 {currency}/CNY 汇率: {'; '.join(errors)}")


def fetch_asset_quote(asset):
    category = normalize_category(asset.get("category"))
    market = normalize_market(asset.get("market"), category)
    symbol = normalize_symbol(asset.get("symbol"), category, market)

    if category == CATEGORY_FUND:
        quote = fetch_fund_quote(symbol)
    elif category == CATEGORY_STOCK:
        quote = fetch_stock_quote(market, symbol)
    else:
        quote = fetch_crypto_quote(symbol)

    currency = str(quote.get("currency") or currency_for(category, market)).upper()
    fx_to_cny, fx_source, fx_estimated = fetch_fx_to_cny(currency)
    quote.update({
        "asset_id": asset.get("asset_id") or asset_id_for(category, market, symbol),
        "category": category,
        "market": market,
        "symbol": symbol,
        "currency": currency,
        "fx_to_cny": fx_to_cny,
        "fx_source": fx_source,
        "fx_estimated": fx_estimated,
        "price_cny": quote["price"] * fx_to_cny,
    })
    if not quote.get("name"):
        quote["name"] = asset.get("name") or symbol
    return quote


def fetch_quotes_for_assets(assets, max_workers=16):
    assets = list(assets)
    if not assets:
        return {}, {}

    quotes = {}
    errors = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, max(len(assets), 1))) as executor:
        future_asset = {
            executor.submit(fetch_asset_quote, asset): asset
            for asset in assets
        }
        for future in as_completed(future_asset):
            asset = future_asset[future]
            asset_id = asset.get("asset_id") or asset_id_for(
                asset.get("category"),
                asset.get("market"),
                asset.get("symbol"),
            )
            try:
                quotes[asset_id] = future.result()
            except Exception as exc:
                errors[asset_id] = str(exc)
    return quotes, errors

