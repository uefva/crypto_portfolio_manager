"""Small server-side formatting and validation helpers."""

from datetime import datetime


def safe_float(value, default=0.0):
    try:
        if value in (None, "", "-", "--"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_trade_date(trade_date=None):
    if trade_date is None or str(trade_date).strip() == "":
        return now_text()

    text = str(trade_date).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    raise ValueError("日期格式不正确，请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS。")


def format_quantity(quantity):
    return f"{quantity:.8f}".rstrip("0").rstrip(".")


def series_value(profit, cost, metric):
    if metric == "收益率":
        return profit / cost * 100 if cost > 0 else 0.0
    return profit
