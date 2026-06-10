"""
基金/ETF 价格查询（增强修正版）
"""

import re
import html
from io import StringIO

import requests
import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# 请求会话
# ============================================================
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
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )

    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# 先直连；失败时再尝试读取系统代理
DIRECT_SESSION = make_session(trust_env=False)
PROXY_SESSION = make_session(trust_env=True)


# ============================================================
# 通用工具
# ============================================================
def to_float(value):
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def request_get(url, params=None, timeout=10, allow_proxy_fallback=True):
    try:
        resp = DIRECT_SESSION.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp
    except Exception:
        if not allow_proxy_fallback:
            raise

    resp = PROXY_SESSION.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp


# ============================================================
# 基金净值
# ============================================================
def extract_eastmoney_table(text, fund_code):
    """
    东方财富 F10DataApi 返回的是：
    var apidata={ content:"<table>...</table>",records:...,pages:...}
    需要先把 content 里的 table 提取出来。
    """
    match = re.search(r'content:"(.*?)",records:', text, re.S)
    if not match:
        raise ValueError(f"{fund_code} 没有找到 content 表格")

    table_html = match.group(1)
    table_html = table_html.replace('\\"', '"').replace("\\/", "/")
    table_html = html.unescape(table_html)

    return table_html


def get_fund_nav(fund_code, rows=5):
    url = "http://fund.eastmoney.com/f10/F10DataApi.aspx"
    params = {
        "type": "lsjz",
        "code": fund_code,
        "page": 1,
        "per": rows,
    }

    resp = request_get(url, params=params, timeout=10)
    resp.encoding = "utf-8"

    table_html = extract_eastmoney_table(resp.text, fund_code)
    dfs = pd.read_html(StringIO(table_html))

    if not dfs:
        raise ValueError(f"{fund_code} 没有解析到净值表")

    df = dfs[0]

    required_cols = {"净值日期", "单位净值", "累计净值"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"{fund_code} 净值表缺少字段: {missing}")

    df["单位净值"] = pd.to_numeric(df["单位净值"], errors="coerce")
    df["累计净值"] = pd.to_numeric(df["累计净值"], errors="coerce")
    df = df.dropna(subset=["单位净值"])

    if len(df) < 2:
        raise ValueError(f"{fund_code} 净值数据不足，无法计算日涨跌")

    return df


def print_fund(index, fund_code, display_name):
    print(f"\n{index}. {display_name} ({fund_code})")

    df = get_fund_nav(fund_code)
    latest = df.iloc[0]
    previous = df.iloc[1]

    latest_nav = latest["单位净值"]
    previous_nav = previous["单位净值"]

    chg = latest_nav - previous_nav
    pct_chg = chg / previous_nav * 100

    print(f"  净值日期: {latest['净值日期']}")
    print(f"  单位净值: {latest_nav:.4f}")
    print(f"  累计净值: {latest['累计净值']:.4f}")
    print(f"  日涨跌:   {chg:+.4f}  ({pct_chg:+.2f}%)")


# ============================================================
# QQQM 行情：东方财富优先，Yahoo 备用
# ============================================================
def get_qqqm_from_eastmoney():
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": 2,
        "fields": "f2,f3,f4,f12,f14,f18",
        "secids": "105.QQQM",
    }

    resp = request_get(url, params=params, timeout=10)
    data = resp.json()

    diff = data.get("data", {}).get("diff", [])
    if not diff:
        raise ValueError("东方财富没有返回 QQQM 行情")

    d = diff[0]

    return {
        "source": "东方财富",
        "code": d.get("f12"),
        "name": d.get("f14"),
        "latest": to_float(d.get("f2")),
        "pct_chg": to_float(d.get("f3")),
        "chg": to_float(d.get("f4")),
        "pre_close": to_float(d.get("f18")),
    }


def get_qqqm_from_yahoo():
    url = "https://query1.finance.yahoo.com/v8/finance/chart/QQQM"
    params = {
        "range": "5d",
        "interval": "1d",
    }

    resp = request_get(url, params=params, timeout=10)
    data = resp.json()

    result = data["chart"]["result"][0]
    meta = result["meta"]

    latest = to_float(meta.get("regularMarketPrice"))
    pre_close = to_float(meta.get("previousClose") or meta.get("chartPreviousClose"))

    if latest is None or pre_close is None:
        raise ValueError("Yahoo 没有返回完整 QQQM 行情")

    chg = latest - pre_close
    pct_chg = chg / pre_close * 100

    return {
        "source": "Yahoo Finance",
        "code": "QQQM",
        "name": "Invesco NASDAQ 100 ETF",
        "latest": latest,
        "pct_chg": pct_chg,
        "chg": chg,
        "pre_close": pre_close,
    }


def get_qqqm_quote():
    try:
        return get_qqqm_from_eastmoney()
    except Exception as eastmoney_error:
        try:
            q = get_qqqm_from_yahoo()
            q["note"] = f"东方财富失败，已切换 Yahoo。原因: {eastmoney_error}"
            return q
        except Exception as yahoo_error:
            raise RuntimeError(
                f"QQQM 获取失败。东方财富错误: {eastmoney_error}; "
                f"Yahoo 错误: {yahoo_error}"
            )


def print_qqqm():
    print("1. QQQM (Invesco Nasdaq 100 ETF)")

    q = get_qqqm_quote()

    print(f"  数据源: {q['source']}")
    print(f"  名称:   {q['name']} ({q['code']})")
    print(f"  最新价: {q['latest']:.4f}")
    print(f"  涨跌额: {q['chg']:+.4f}")
    print(f"  涨跌幅: {q['pct_chg']:+.2f}%")
    print(f"  昨收:   {q['pre_close']:.4f}")

    if q.get("note"):
        print(f"  备注:   {q['note']}")


# ============================================================
# 主程序
# ============================================================
def main():
    try:
        print_qqqm()
    except Exception as e:
        print("1. QQQM (Invesco Nasdaq 100 ETF)")
        print(f"  获取失败: {e}")

    funds = [
        ("270042", "广发纳斯达克100ETF联接A"),
        ("017437", "华宝纳斯达克精选股票C"),
        ("009478", "中银上海金ETF联接C"),
    ]

    for index, (code, name) in enumerate(funds, start=2):
        try:
            print_fund(index, code, name)
        except Exception as e:
            print(f"\n{index}. {name} ({code})")
            print(f"  获取失败: {e}")


if __name__ == "__main__":
    main()