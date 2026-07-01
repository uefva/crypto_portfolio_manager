"""Tkinter desktop application for portfolio management.

The desktop app is intentionally kept behavior-compatible in this structural
split; future work can move each notebook tab into the ``desktop.tabs`` package.
"""

import configparser
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import requests

from crypto_portfolio.market_data import (
    CATEGORY_ALL,
    CATEGORY_CRYPTO,
    CATEGORY_FUND,
    CATEGORY_STOCK,
    CATEGORIES,
    COIN_MAP,
    MARKET_CRYPTO,
    MARKET_FUND,
    MARKET_HK,
    MARKET_LABELS,
    MARKET_SH,
    MARKET_SZ,
    MARKET_US,
    currency_for,
    suggestion_label,
    normalize_category,
    normalize_market,
    normalize_symbol,
)
from crypto_portfolio.portfolio_api_client import PortfolioApiClient
from crypto_portfolio.portfolio_manager import PortfolioManager


MARKET_DISPLAY_BY_CODE = {
    code: label for code, label in MARKET_LABELS.items()
}
MARKET_CODE_BY_DISPLAY = {
    label: code for code, label in MARKET_DISPLAY_BY_CODE.items()
}
CATEGORY_OPTIONS = (CATEGORY_FUND, CATEGORY_STOCK, CATEGORY_CRYPTO)
CATEGORY_FILTER_OPTIONS = (CATEGORY_ALL, *CATEGORY_OPTIONS)
DEFAULT_GUI_CONFIG_PATH = "gui_config.ini"
DEFAULT_SERVER_URL = "http://127.0.0.1:8765"


def load_gui_server_url(config_path=DEFAULT_GUI_CONFIG_PATH):
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    url = parser.get("server", "url", fallback=DEFAULT_SERVER_URL).strip()
    return url or DEFAULT_SERVER_URL


class PortfolioApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("多资产持仓管理")
        self.geometry("1320x780")
        self.minsize(1080, 660)

        self.local_manager = PortfolioManager()
        self.manager = PortfolioApiClient(load_gui_server_url(), fallback=self.local_manager)
        self.selected_transaction = None
        self.selected_asset_id = None
        self.latest_quotes = {}
        self.symbol_display_to_asset = {}

        self.status_var = tk.StringVar(value="就绪")
        self.holding_category_var = tk.StringVar(value=CATEGORY_ALL)
        self.tx_category_filter_var = tk.StringVar(value=CATEGORY_ALL)
        self.holding_summary_var = tk.StringVar(value="")
        self.tx_summary_var = tk.StringVar(value="点击“刷新收益”查看当前总资产收益")
        self.snapshot_summary_var = tk.StringVar(value="")

        self.category_var = tk.StringVar(value=CATEGORY_CRYPTO)
        self.market_var = tk.StringVar(value=MARKET_DISPLAY_BY_CODE[MARKET_CRYPTO])
        self.symbol_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.tx_type_var = tk.StringVar(value="买入")
        self.amount_var = tk.StringVar()
        self.price_var = tk.StringVar()
        self.currency_var = tk.StringVar(value="USD")
        self.date_var = tk.StringVar(value=self.manager.now())
        self.asset_category_var = tk.StringVar(value=CATEGORY_FUND)
        self.asset_market_var = tk.StringVar(value=MARKET_DISPLAY_BY_CODE[MARKET_FUND])
        self.asset_symbol_var = tk.StringVar()
        self.asset_name_var = tk.StringVar()

        self.chart_source_var = tk.StringVar(value="服务端价格记录")
        self.chart_metric_var = tk.StringVar(value="收益金额")
        self.chart_range_var = tk.StringVar(value="全部时间")
        self.server_url_var = tk.StringVar(value=load_gui_server_url())
        self.profit_chart_data = None
        self.profit_chart_layout = None
        self.highlighted_series = None
        self.chart_series_vars = {}
        self.chart_series_meta = {}

        self.create_widgets()
        self.update_asset_market_options()
        self.update_form_market_options()
        self.refresh_all()

    def create_widgets(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        self.holdings_tab = ttk.Frame(self.notebook)
        self.assets_tab = ttk.Frame(self.notebook)
        self.transactions_tab = ttk.Frame(self.notebook)
        self.snapshots_tab = ttk.Frame(self.notebook)
        self.profit_chart_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.holdings_tab, text="持仓")
        self.notebook.add(self.assets_tab, text="资产管理")
        self.notebook.add(self.transactions_tab, text="交易记录")
        self.notebook.add(self.snapshots_tab, text="历史持仓结果")
        self.notebook.add(self.profit_chart_tab, text="收益走势")

        self.create_holdings_tab()
        self.create_assets_tab()
        self.create_transactions_tab()
        self.create_snapshots_tab()
        self.create_profit_chart_tab()

        status = ttk.Label(self, textvariable=self.status_var, anchor="w")
        status.pack(fill="x", padx=10, pady=8)

    def pack_tree_with_horizontal_scrollbar(self, tree):
        scrollbar = ttk.Scrollbar(tree.master, orient="horizontal", command=tree.xview)
        tree.configure(xscrollcommand=scrollbar.set)
        tree.pack(fill="both", expand=True)
        scrollbar.pack(fill="x")

    def create_holdings_tab(self):
        toolbar = ttk.Frame(self.holdings_tab)
        toolbar.pack(fill="x", pady=(0, 8))

        ttk.Label(toolbar, text="类别").pack(side="left")
        filter_combo = ttk.Combobox(
            toolbar,
            textvariable=self.holding_category_var,
            values=CATEGORY_FILTER_OPTIONS,
            width=12,
            state="readonly",
        )
        filter_combo.pack(side="left", padx=8)
        filter_combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_holdings_filter())

        self.refresh_holdings_button = ttk.Button(
            toolbar,
            text="查询并保存",
            command=self.refresh_holdings,
        )
        self.refresh_holdings_button.pack(side="left")
        ttk.Button(toolbar, text="刷新本地数据", command=self.refresh_all).pack(side="left", padx=8)

        columns = (
            "category", "market", "symbol", "name", "quantity", "avg_cost", "price",
            "currency", "fx", "value_cny", "cost_cny", "profit_cny", "rate",
        )
        self.holdings_tree = ttk.Treeview(
            self.holdings_tab,
            columns=columns,
            show="headings",
            height=18,
        )
        headings = {
            "category": "类别",
            "market": "市场",
            "symbol": "代码",
            "name": "名称",
            "quantity": "数量",
            "avg_cost": "成本价",
            "price": "当前价",
            "currency": "币种",
            "fx": "汇率",
            "value_cny": "持仓价值(CNY)",
            "cost_cny": "成本(CNY)",
            "profit_cny": "收益(CNY)",
            "rate": "收益率",
        }
        widths = {
            "category": 80,
            "market": 90,
            "symbol": 90,
            "name": 160,
            "quantity": 120,
            "avg_cost": 100,
            "price": 100,
            "currency": 70,
            "fx": 80,
            "value_cny": 130,
            "cost_cny": 120,
            "profit_cny": 120,
            "rate": 90,
        }
        for column in columns:
            anchor = "w" if column in {"category", "market", "symbol", "name", "currency"} else "e"
            self.holdings_tree.heading(column, text=headings[column])
            self.holdings_tree.column(column, width=widths[column], anchor=anchor)
        self.configure_rate_tags(self.holdings_tree)
        self.configure_sortable_tree(self.holdings_tree, headings)
        self.pack_tree_with_horizontal_scrollbar(self.holdings_tree)
        ttk.Label(self.holdings_tab, textvariable=self.holding_summary_var, anchor="w").pack(
            fill="x", pady=(8, 0)
        )

    def create_assets_tab(self):
        form = ttk.LabelFrame(self.assets_tab, text="资产")
        form.pack(fill="x", pady=(0, 8))

        ttk.Label(form, text="类别").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        asset_category_combo = ttk.Combobox(
            form,
            textvariable=self.asset_category_var,
            values=CATEGORY_OPTIONS,
            width=12,
            state="readonly",
        )
        asset_category_combo.grid(row=0, column=1, padx=6, pady=6, sticky="we")
        asset_category_combo.bind("<<ComboboxSelected>>", lambda _event: self.update_asset_market_options())

        ttk.Label(form, text="市场").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.asset_market_combo = ttk.Combobox(
            form,
            textvariable=self.asset_market_var,
            width=12,
            state="readonly",
        )
        self.asset_market_combo.grid(row=0, column=3, padx=6, pady=6, sticky="we")

        ttk.Label(form, text="代码").grid(row=0, column=4, padx=6, pady=6, sticky="w")
        ttk.Entry(form, textvariable=self.asset_symbol_var, width=16).grid(
            row=0, column=5, padx=6, pady=6, sticky="we"
        )

        ttk.Label(form, text="名称").grid(row=0, column=6, padx=6, pady=6, sticky="w")
        ttk.Entry(form, textvariable=self.asset_name_var, width=24).grid(
            row=0, column=7, padx=6, pady=6, sticky="we"
        )

        buttons = ttk.Frame(form)
        buttons.grid(row=1, column=5, columnspan=3, padx=6, pady=6, sticky="e")
        ttk.Button(buttons, text="新增", command=self.add_asset).pack(side="left")
        ttk.Button(buttons, text="保存修改", command=self.update_asset).pack(side="left", padx=8)
        ttk.Button(buttons, text="删除空资产", command=self.delete_selected_asset).pack(side="left")
        ttk.Button(buttons, text="导入本地数据到服务端", command=self.import_local_portfolio_to_server).pack(side="left", padx=8)
        ttk.Button(buttons, text="清空", command=self.clear_asset_form).pack(side="left", padx=(8, 0))

        for column in range(8):
            form.columnconfigure(column, weight=1)

        columns = ("category", "market", "symbol", "name", "currency", "quantity", "transactions", "asset_id")
        self.assets_tree = ttk.Treeview(
            self.assets_tab,
            columns=columns,
            show="headings",
            height=20,
        )
        headings = {
            "category": "类别",
            "market": "市场",
            "symbol": "代码",
            "name": "名称",
            "currency": "币种",
            "quantity": "持仓数量",
            "transactions": "交易数",
            "asset_id": "资产ID",
        }
        widths = {
            "category": 80,
            "market": 100,
            "symbol": 120,
            "name": 220,
            "currency": 70,
            "quantity": 120,
            "transactions": 80,
            "asset_id": 1,
        }
        for column in columns:
            anchor = "w" if column in {"category", "market", "symbol", "name", "currency"} else "e"
            self.assets_tree.heading(column, text=headings[column])
            self.assets_tree.column(column, width=widths[column], anchor=anchor, stretch=column != "asset_id")
        self.assets_tree.column("asset_id", width=1, minwidth=1, stretch=False)
        self.configure_sortable_tree(self.assets_tree, headings)
        self.pack_tree_with_horizontal_scrollbar(self.assets_tree)
        self.assets_tree.bind("<<TreeviewSelect>>", self.on_asset_select)

    def create_transactions_tab(self):
        summary_bar = ttk.Frame(self.transactions_tab)
        summary_bar.pack(fill="x", pady=(0, 8))
        ttk.Label(summary_bar, text="筛选").pack(side="left")
        filter_combo = ttk.Combobox(
            summary_bar,
            textvariable=self.tx_category_filter_var,
            values=CATEGORY_FILTER_OPTIONS,
            width=12,
            state="readonly",
        )
        filter_combo.pack(side="left", padx=8)
        filter_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_transactions())
        ttk.Button(summary_bar, text="刷新收益", command=self.refresh_transaction_summary).pack(side="left")
        ttk.Label(summary_bar, textvariable=self.tx_summary_var, anchor="w").pack(
            side="left", padx=12, fill="x", expand=True
        )

        form = ttk.LabelFrame(self.transactions_tab, text="新增 / 编辑交易")
        form.pack(fill="x", pady=(0, 8))

        ttk.Label(form, text="类别").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        category_combo = ttk.Combobox(
            form,
            textvariable=self.category_var,
            values=CATEGORY_OPTIONS,
            width=12,
            state="readonly",
        )
        category_combo.grid(row=0, column=1, padx=6, pady=6, sticky="we")
        category_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_trade_category_changed())

        ttk.Label(form, text="市场").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.market_combo = ttk.Combobox(
            form,
            textvariable=self.market_var,
            width=12,
            state="readonly",
        )
        self.market_combo.grid(row=0, column=3, padx=6, pady=6, sticky="we")
        self.market_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_trade_market_changed())

        ttk.Label(form, text="代码").grid(row=0, column=4, padx=6, pady=6, sticky="w")
        self.symbol_combo = ttk.Combobox(form, textvariable=self.symbol_var, width=24)
        self.symbol_combo.grid(row=0, column=5, padx=6, pady=6, sticky="we")
        self.symbol_combo.bind("<<ComboboxSelected>>", self.on_symbol_selected)
        self.symbol_combo.bind("<KeyRelease>", self.on_symbol_keyrelease)

        ttk.Label(form, text="类型").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        ttk.Combobox(
            form,
            textvariable=self.tx_type_var,
            values=("买入", "卖出"),
            width=12,
            state="readonly",
        ).grid(row=1, column=1, padx=6, pady=6, sticky="we")

        self.amount_label = ttk.Label(form, text="数量")
        self.amount_label.grid(row=1, column=2, padx=6, pady=6, sticky="w")
        ttk.Entry(form, textvariable=self.amount_var, width=16).grid(
            row=1, column=3, padx=6, pady=6, sticky="we"
        )

        self.price_label = ttk.Label(form, text="价格")
        self.price_label.grid(row=1, column=4, padx=6, pady=6, sticky="w")
        ttk.Entry(form, textvariable=self.price_var, width=16).grid(
            row=1, column=5, padx=6, pady=6, sticky="we"
        )

        ttk.Label(form, text="币种").grid(row=1, column=6, padx=6, pady=6, sticky="w")
        ttk.Label(form, textvariable=self.currency_var, width=8).grid(
            row=1, column=7, padx=6, pady=6, sticky="w"
        )

        ttk.Label(form, text="日期").grid(row=2, column=0, padx=6, pady=6, sticky="w")
        ttk.Entry(form, textvariable=self.date_var, width=22).grid(
            row=2, column=1, columnspan=4, padx=6, pady=6, sticky="we"
        )

        buttons = ttk.Frame(form)
        buttons.grid(row=2, column=5, columnspan=3, padx=6, pady=6, sticky="e")
        ttk.Button(buttons, text="新增", command=self.add_transaction).pack(side="left")
        ttk.Button(buttons, text="保存修改", command=self.update_transaction).pack(side="left", padx=8)
        ttk.Button(buttons, text="删除选中", command=self.delete_selected_transaction).pack(side="left")
        ttk.Button(buttons, text="清空", command=self.clear_transaction_form).pack(side="left", padx=(8, 0))

        for column in range(8):
            form.columnconfigure(column, weight=1)

        columns = (
            "category", "market", "symbol", "name", "index", "type", "date",
            "amount", "price", "currency", "total", "asset_id",
        )
        self.transactions_tree = ttk.Treeview(
            self.transactions_tab,
            columns=columns,
            show="headings",
            height=16,
        )
        headings = {
            "category": "类别",
            "market": "市场",
            "symbol": "代码",
            "name": "名称",
            "index": "序号",
            "type": "类型",
            "date": "日期",
            "amount": "数量",
            "price": "价格",
            "currency": "币种",
            "total": "成交金额",
            "asset_id": "资产ID",
        }
        widths = {
            "category": 80,
            "market": 90,
            "symbol": 90,
            "name": 150,
            "index": 60,
            "type": 70,
            "date": 160,
            "amount": 110,
            "price": 100,
            "currency": 60,
            "total": 120,
            "asset_id": 1,
        }
        for column in columns:
            anchor = "w" if column in {"category", "market", "symbol", "name", "type", "date", "currency"} else "e"
            self.transactions_tree.heading(column, text=headings[column])
            self.transactions_tree.column(column, width=widths[column], anchor=anchor, stretch=column != "asset_id")
        self.transactions_tree.column("asset_id", width=1, minwidth=1, stretch=False)
        self.configure_sortable_tree(self.transactions_tree, headings)
        self.pack_tree_with_horizontal_scrollbar(self.transactions_tree)
        self.transactions_tree.bind("<<TreeviewSelect>>", self.on_transaction_select)

    def create_snapshots_tab(self):
        outer = ttk.PanedWindow(self.snapshots_tab, orient="horizontal")
        outer.pack(fill="both", expand=True)

        left = ttk.Frame(outer)
        right = ttk.Frame(outer)
        outer.add(left, weight=1)
        outer.add(right, weight=3)

        left_toolbar = ttk.Frame(left)
        left_toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(left_toolbar, text="刷新列表", command=self.refresh_snapshots).pack(side="left")
        ttk.Button(left_toolbar, text="删除快照", command=self.delete_selected_snapshot).pack(
            side="left", padx=8
        )

        self.snapshots_tree = ttk.Treeview(
            left,
            columns=("saved_at", "value", "profit", "path"),
            show="headings",
            height=18,
        )
        snapshot_headings = {}
        for column, title, width in (
            ("saved_at", "查询时间", 170),
            ("value", "总价值(CNY)", 120),
            ("profit", "总收益(CNY)", 120),
            ("path", "文件", 240),
        ):
            snapshot_headings[column] = title
            self.snapshots_tree.heading(column, text=title)
            self.snapshots_tree.column(column, width=width, anchor="w")
        self.configure_sortable_tree(self.snapshots_tree, snapshot_headings)
        self.pack_tree_with_horizontal_scrollbar(self.snapshots_tree)
        self.snapshots_tree.bind("<<TreeviewSelect>>", self.on_snapshot_select)

        detail_columns = (
            "category", "market", "symbol", "name", "quantity", "avg_cost", "price",
            "currency", "fx", "value_cny", "cost_cny", "profit_cny", "rate",
        )
        self.snapshot_detail_tree = ttk.Treeview(
            right,
            columns=detail_columns,
            show="headings",
        )
        detail_headings = {
            "category": "类别",
            "market": "市场",
            "symbol": "代码",
            "name": "名称",
            "quantity": "数量",
            "avg_cost": "成本价",
            "price": "当前价",
            "currency": "币种",
            "fx": "汇率",
            "value_cny": "持仓价值(CNY)",
            "cost_cny": "成本(CNY)",
            "profit_cny": "收益(CNY)",
            "rate": "收益率",
        }
        for column, title in detail_headings.items():
            anchor = "w" if column in {"category", "market", "symbol", "name", "currency"} else "e"
            self.snapshot_detail_tree.heading(column, text=title)
            self.snapshot_detail_tree.column(column, width=110, anchor=anchor)
        self.configure_rate_tags(self.snapshot_detail_tree)
        self.configure_sortable_tree(self.snapshot_detail_tree, detail_headings)
        self.pack_tree_with_horizontal_scrollbar(self.snapshot_detail_tree)
        ttk.Label(right, textvariable=self.snapshot_summary_var, anchor="w").pack(
            fill="x", pady=(8, 0)
        )

    def create_profit_chart_tab(self):
        toolbar = ttk.Frame(self.profit_chart_tab)
        toolbar.pack(fill="x", pady=(0, 8))

        ttk.Label(toolbar, text="数据源").pack(side="left")
        source_combo = ttk.Combobox(
            toolbar,
            textvariable=self.chart_source_var,
            values=("历史仓位结果", "服务端价格记录"),
            width=14,
            state="readonly",
        )
        source_combo.pack(side="left", padx=8)
        source_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_profit_chart())

        ttk.Label(toolbar, text="指标").pack(side="left")
        metric_combo = ttk.Combobox(
            toolbar,
            textvariable=self.chart_metric_var,
            values=("收益金额", "收益率"),
            width=12,
            state="readonly",
        )
        metric_combo.pack(side="left", padx=8)
        metric_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_profit_chart())

        ttk.Label(toolbar, text="范围").pack(side="left")
        range_combo = ttk.Combobox(
            toolbar,
            textvariable=self.chart_range_var,
            values=("过去一天", "过去一周", "过去一个月", "过去半年", "过去一年", "全部时间"),
            width=12,
            state="readonly",
        )
        range_combo.pack(side="left", padx=8)
        range_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_profit_chart())

        ttk.Label(toolbar, text="服务端").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.server_url_var, width=28).pack(side="left", padx=8)
        ttk.Button(toolbar, text="刷新图表", command=self.refresh_profit_chart).pack(side="left")
        ttk.Button(toolbar, text="总资产", command=lambda: self.select_chart_group(CATEGORY_ALL)).pack(
            side="left", padx=(8, 0)
        )
        for category in CATEGORY_OPTIONS:
            ttk.Button(
                toolbar,
                text=category,
                command=lambda value=category: self.select_chart_group(value),
            ).pack(side="left", padx=(4, 0))
        ttk.Button(toolbar, text="清除高亮", command=self.clear_chart_highlight).pack(
            side="left", padx=8
        )

        chart_body = ttk.Frame(self.profit_chart_tab)
        chart_body.pack(fill="both", expand=True)

        self.chart_symbol_panel = ttk.LabelFrame(chart_body, text="展示曲线", padding=(8, 6))
        self.chart_symbol_panel.pack(side="right", fill="y", padx=(8, 0))
        self.chart_symbol_panel.pack_propagate(False)
        self.chart_symbol_panel.configure(width=190)
        self.chart_symbol_canvas = tk.Canvas(
            self.chart_symbol_panel,
            highlightthickness=0,
            width=166,
        )
        self.chart_symbol_scrollbar = ttk.Scrollbar(
            self.chart_symbol_panel,
            orient="vertical",
            command=self.chart_symbol_canvas.yview,
        )
        self.chart_symbol_canvas.configure(yscrollcommand=self.chart_symbol_scrollbar.set)
        self.chart_symbol_scrollbar.pack(side="right", fill="y")
        self.chart_symbol_canvas.pack(side="left", fill="both", expand=True)
        self.chart_symbol_list_frame = ttk.Frame(self.chart_symbol_canvas)
        self.chart_symbol_window = self.chart_symbol_canvas.create_window(
            (0, 0),
            window=self.chart_symbol_list_frame,
            anchor="nw",
        )
        self.chart_symbol_list_frame.bind(
            "<Configure>",
            lambda _event: self.chart_symbol_canvas.configure(
                scrollregion=self.chart_symbol_canvas.bbox("all")
            ),
        )
        self.chart_symbol_canvas.bind("<Configure>", self.on_chart_symbol_canvas_configure)
        self.chart_symbol_canvas.bind("<MouseWheel>", self.on_chart_symbol_mousewheel)

        self.profit_chart_canvas = tk.Canvas(
            chart_body,
            background="white",
            highlightthickness=1,
            highlightbackground="#d0d0d0",
        )
        self.profit_chart_canvas.pack(side="left", fill="both", expand=True)
        self.profit_chart_canvas.bind("<Configure>", lambda _event: self.draw_profit_chart())
        self.profit_chart_canvas.bind("<Motion>", self.on_profit_chart_motion)
        self.profit_chart_canvas.bind("<Leave>", lambda _event: self.clear_chart_hover())

    def refresh_all(self):
        if hasattr(self.manager, "server_url"):
            self.manager.server_url = self.normalize_server_url()
        self.manager.data = self.manager.load_data()
        self.refresh_assets()
        self.refresh_symbols()
        self.refresh_transactions()
        self.refresh_snapshots()
        if getattr(self.manager, "online", False):
            self.status_var.set("服务端数据已刷新")
        else:
            self.status_var.set("服务端不可用，已使用本地只读数据")

    def refresh_symbols(self):
        suggestions = self.manager.asset_suggestions(
            self.symbol_var.get(),
            self.category_var.get(),
            self.current_form_market(),
        )
        if normalize_category(self.category_var.get()) == CATEGORY_CRYPTO:
            suggestions.extend({
                "category": CATEGORY_CRYPTO,
                "market": MARKET_CRYPTO,
                "symbol": symbol,
                "name": symbol,
                "currency": "USD",
                "asset_id": f"crypto:CRYPTO:{symbol}",
            } for symbol in COIN_MAP)

        self.symbol_display_to_asset = {}
        values = []
        seen = set()
        for asset in suggestions:
            display = suggestion_label(asset)
            asset_id = asset.get("asset_id")
            if display in seen:
                continue
            seen.add(display)
            values.append(display)
            self.symbol_display_to_asset[display] = asset
            if asset_id:
                self.symbol_display_to_asset[asset_id] = asset
            self.symbol_display_to_asset[asset.get("symbol", "")] = asset
        self.symbol_combo["values"] = values

    def current_form_market(self):
        return MARKET_CODE_BY_DISPLAY.get(self.market_var.get(), self.market_var.get())

    def current_asset_market(self):
        return MARKET_CODE_BY_DISPLAY.get(self.asset_market_var.get(), self.asset_market_var.get())

    def markets_for_category(self, category):
        category = normalize_category(category)
        if category == CATEGORY_FUND:
            return [MARKET_FUND]
        if category == CATEGORY_STOCK:
            return [MARKET_SH, MARKET_SZ, MARKET_HK, MARKET_US]
        return [MARKET_CRYPTO]

    def update_form_market_options(self):
        category = normalize_category(self.category_var.get())
        markets = self.markets_for_category(category)
        displays = [MARKET_DISPLAY_BY_CODE[market] for market in markets]
        self.market_combo["values"] = displays
        if self.market_var.get() not in displays:
            self.market_var.set(displays[0])
        self.update_currency_from_form()
        self.update_trade_amount_labels()
        self.refresh_symbols()

    def update_asset_market_options(self):
        category = normalize_category(self.asset_category_var.get())
        markets = self.markets_for_category(category)
        displays = [MARKET_DISPLAY_BY_CODE[market] for market in markets]
        self.asset_market_combo["values"] = displays
        if self.asset_market_var.get() not in displays:
            self.asset_market_var.set(displays[0])

    def on_trade_category_changed(self):
        self.symbol_var.set("")
        self.name_var.set("")
        self.update_form_market_options()

    def on_trade_market_changed(self):
        self.symbol_var.set("")
        self.name_var.set("")
        self.update_currency_from_form()
        self.refresh_symbols()

    def update_currency_from_form(self):
        category = normalize_category(self.category_var.get())
        market = normalize_market(self.current_form_market(), category)
        currency = currency_for(category, market)
        self.currency_var.set(currency)

    def update_trade_amount_labels(self):
        if normalize_category(self.category_var.get()) == CATEGORY_FUND:
            self.amount_label.configure(text="确认份额")
            self.price_label.configure(text="确认净值")
        else:
            self.amount_label.configure(text="数量")
            self.price_label.configure(text="价格")

    def on_symbol_keyrelease(self, _event):
        self.refresh_symbols()

    def on_symbol_selected(self, _event=None):
        asset = self.symbol_display_to_asset.get(self.symbol_var.get())
        if not asset:
            return
        self.symbol_var.set(suggestion_label(asset))
        self.name_var.set(asset.get("name") or asset.get("symbol", ""))
        self.currency_var.set(asset.get("currency") or currency_for(asset.get("category"), asset.get("market")))

    def selected_trade_asset(self):
        text = self.symbol_var.get().strip()
        asset = self.symbol_display_to_asset.get(text)
        if asset:
            return asset
        category = normalize_category(self.category_var.get())
        market = normalize_market(self.current_form_market(), category)
        symbol = normalize_symbol(text, category, market)
        asset_id = self.manager.find_asset_id(symbol, category, market)
        existing = self.manager.data.get(asset_id)
        if existing:
            return existing
        return {
            "asset_id": asset_id,
            "category": category,
            "market": market,
            "symbol": symbol,
            "name": self.name_var.get().strip() or symbol,
            "currency": currency_for(category, market),
        }

    def apply_holdings_filter(self):
        if self.latest_quotes:
            snapshot = self.manager.build_holdings_snapshot(
                self.latest_quotes,
                self.holding_category_var.get(),
            )
            self.fill_tree(self.holdings_tree, snapshot["rows"], self.rate_tag_for_row)
            self.holding_summary_var.set(self.format_snapshot_summary(snapshot))

    def refresh_holdings(self):
        if not self.manager.data:
            messagebox.showinfo("提示", "暂无持仓。")
            return

        self.refresh_holdings_button.configure(state="disabled")

        def task():
            assets = self.manager.get_assets(self.holding_category_var.get(), active_only=True)
            quotes = self.manager.get_latest_quotes(assets)
            snapshot = self.manager.build_holdings_snapshot(quotes, self.holding_category_var.get())
            snapshot_path = self.manager.save_holdings_snapshot(snapshot)
            return quotes, snapshot, snapshot_path

        def on_success(result):
            quotes, snapshot, snapshot_path = result
            self.latest_quotes.update(quotes)
            self.fill_tree(self.holdings_tree, snapshot["rows"], self.rate_tag_for_row)
            self.holding_summary_var.set(self.format_snapshot_summary(snapshot))
            self.refresh_snapshots()
            self.status_var.set(f"持仓查询已保存: {snapshot_path}")

        def on_done():
            self.refresh_holdings_button.configure(state="normal")

        self.run_background(task, on_success, "正在查询价格...", on_done=on_done)

    def refresh_transaction_summary(self):
        if not self.manager.data:
            self.tx_summary_var.set("暂无持仓")
            return

        category = self.tx_category_filter_var.get()

        def task():
            return self.manager.build_portfolio_summary(category_filter=category)

        def on_success(summary):
            unknown = summary.get("unknown_price_symbols", [])
            text = (
                f"总价值: ¥{summary['total_value']:.2f}    "
                f"成本: ¥{summary['total_cost']:.2f}    "
                f"收益: {summary['total_profit']:.2f} "
                f"({summary['total_profit_rate']:.2f}%)"
            )
            if unknown:
                text += f"    未计入: {', '.join(unknown)}"
            self.tx_summary_var.set(text)

        self.run_background(task, on_success, "正在计算总资产收益...")

    def refresh_assets(self):
        if not hasattr(self, "assets_tree"):
            return
        rows = []
        for asset in self.manager.get_assets():
            rows.append((
                asset["category"],
                MARKET_LABELS.get(asset["market"], asset["market"]),
                asset["symbol"],
                asset.get("name", asset["symbol"]),
                asset.get("currency", currency_for(asset["category"], asset["market"])),
                self.manager.format_quantity(asset.get("quantity", 0)),
                len(asset.get("transactions", [])),
                asset["asset_id"],
            ))
        self.fill_tree(self.assets_tree, rows)

    def add_asset(self):
        asset = self.manager.upsert_asset(
            self.asset_category_var.get(),
            self.current_asset_market(),
            self.asset_symbol_var.get(),
            self.asset_name_var.get(),
        )
        if asset:
            self.clear_asset_form()
            self.after_data_change("资产已保存")
        else:
            messagebox.showwarning("未保存", "资产没有保存，请检查代码和名称。")

    def update_asset(self):
        if not self.selected_asset_id:
            messagebox.showinfo("提示", "请先选择一个资产。")
            return

        if self.manager.update_asset(
            self.selected_asset_id,
            self.asset_category_var.get(),
            self.current_asset_market(),
            self.asset_symbol_var.get(),
            self.asset_name_var.get(),
        ):
            self.clear_asset_form()
            self.after_data_change("资产已修改")
        else:
            messagebox.showwarning("未保存", "修改失败。有交易记录的资产只能修改名称。")

    def delete_selected_asset(self):
        if not self.selected_asset_id:
            messagebox.showinfo("提示", "请先选择一个资产。")
            return
        if not messagebox.askyesno("确认删除", "确认删除选中的空资产？"):
            return

        if self.manager.delete_asset(self.selected_asset_id):
            self.clear_asset_form()
            self.after_data_change("资产已删除")
        else:
            messagebox.showwarning("未删除", "只能删除没有交易记录的资产。")

    def on_asset_select(self, _event):
        selection = self.assets_tree.selection()
        if not selection:
            return

        category, market_label, symbol, name, _currency, _quantity, _transactions, asset_id = (
            self.assets_tree.item(selection[0], "values")
        )
        self.selected_asset_id = asset_id
        self.asset_category_var.set(category)
        self.update_asset_market_options()
        self.asset_market_var.set(market_label)
        self.asset_symbol_var.set(symbol)
        self.asset_name_var.set(name)

    def clear_asset_form(self):
        self.selected_asset_id = None
        self.asset_category_var.set(CATEGORY_FUND)
        self.update_asset_market_options()
        self.asset_symbol_var.set("")
        self.asset_name_var.set("")
        if hasattr(self, "assets_tree"):
            self.assets_tree.selection_remove(self.assets_tree.selection())

    def import_local_portfolio_to_server(self):
        if not hasattr(self.manager, "import_local_portfolio"):
            messagebox.showinfo("提示", "当前模式不支持导入到服务端。")
            return
        if not messagebox.askyesno("确认导入", "确认将本地 portfolio.json 导入服务端？重复交易会自动跳过。"):
            return
        try:
            report = self.manager.import_local_portfolio()
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        if report is None:
            messagebox.showwarning("导入失败", "未找到本地 portfolio.json。")
            return
        self.refresh_all()
        messagebox.showinfo(
            "导入完成",
            (
                f"新增资产: {report.get('assets_imported', 0)}\n"
                f"更新资产: {report.get('assets_updated', 0)}\n"
                f"新增交易: {report.get('transactions_imported', 0)}\n"
                f"跳过交易: {report.get('transactions_skipped', 0)}"
            ),
        )

    def run_background(self, task, on_success, busy_message, on_done=None):
        self.status_var.set(busy_message)

        def worker():
            try:
                result = task()
            except Exception as exc:
                self.after(0, lambda error=exc: self.handle_background_error(error, on_done))
                return
            self.after(0, lambda value=result: self.handle_background_success(value, on_success, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def handle_background_success(self, result, on_success, on_done):
        try:
            on_success(result)
        finally:
            if on_done:
                on_done()

    def handle_background_error(self, error, on_done):
        if on_done:
            on_done()
        self.status_var.set("后台任务失败")
        messagebox.showerror("操作失败", str(error))

    def refresh_transactions(self):
        category = self.tx_category_filter_var.get()
        transactions = self.manager.get_transactions(category=category)
        rows = []
        for tx in transactions:
            rows.append((
                tx["category"],
                tx["market_label"],
                tx["symbol"],
                tx["name"],
                tx["index"],
                "买入" if tx["type"] == "buy" else "卖出",
                tx["date"],
                tx["amount"],
                tx["price"],
                tx["currency"],
                f"{float(tx['total']):.4f}",
                tx["asset_id"],
            ))
        self.fill_tree(self.transactions_tree, rows)

    def refresh_snapshots(self):
        rows = []
        for path, snapshot in self.manager.list_holdings_snapshots():
            rows.append((
                snapshot.get("saved_at", "未知"),
                f"{snapshot.get('total_value', 0.0):.2f}",
                f"{snapshot.get('total_profit', 0.0):.2f}",
                str(path),
            ))
        self.fill_tree(self.snapshots_tree, rows)
        if hasattr(self, "profit_chart_canvas"):
            self.refresh_profit_chart()

    def fill_tree(self, tree, rows, tag_factory=None):
        for item in tree.get_children():
            tree.delete(item)
        for row in rows:
            tags = tag_factory(row) if tag_factory else ()
            tree.insert("", "end", values=row, tags=tags)
        self.reset_sort_headings(tree)

    def configure_sortable_tree(self, tree, headings):
        tree.sort_headings = headings
        tree.sort_state = {}
        for column, title in headings.items():
            tree.heading(
                column,
                text=title,
                command=lambda current_column=column: self.sort_tree(tree, current_column),
            )

    def reset_sort_headings(self, tree):
        headings = getattr(tree, "sort_headings", None)
        if not headings:
            return
        tree.sort_state = {}
        for column, title in headings.items():
            tree.heading(
                column,
                text=title,
                command=lambda current_column=column: self.sort_tree(tree, current_column),
            )

    def sort_tree(self, tree, column):
        headings = getattr(tree, "sort_headings", {})
        current_descending = getattr(tree, "sort_state", {}).get(column, True)
        descending = not current_descending

        items = list(tree.get_children(""))
        items.sort(
            key=lambda item: self.sort_value(tree.set(item, column)),
            reverse=descending,
        )
        for index, item in enumerate(items):
            tree.move(item, "", index)

        tree.sort_state = {column: descending}
        for current_column, title in headings.items():
            suffix = ""
            if current_column == column:
                suffix = " ↓" if descending else " ↑"
            tree.heading(
                current_column,
                text=f"{title}{suffix}",
                command=lambda selected=current_column: self.sort_tree(tree, selected),
            )

    def sort_value(self, value):
        text = str(value).strip()
        if text in {"", "无法计算", "价格未知", "未知"}:
            return (3, "")

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return (0, datetime.strptime(text, fmt))
            except ValueError:
                pass

        try:
            return (1, self.parse_metric_value(text))
        except ValueError:
            return (2, text.upper())

    def parse_metric_value(self, value):
        text = str(value).strip().replace(",", "").replace("$", "").replace("¥", "")
        if text.endswith("%"):
            text = text[:-1]
        return float(text)

    def configure_rate_tags(self, tree):
        tree.tag_configure("profit_positive", foreground="#c62828")
        tree.tag_configure("profit_negative", foreground="#2e7d32")
        tree.tag_configure("profit_neutral", foreground="#555555")
        tree.tag_configure("profit_unknown", foreground="#777777")

    def rate_tag_for_row(self, row):
        if len(row) < 13:
            return ()
        rate_text = str(row[12]).strip()
        if not rate_text.endswith("%"):
            return ("profit_unknown",)
        try:
            rate = float(rate_text.rstrip("%"))
        except ValueError:
            return ("profit_unknown",)
        if rate > 0:
            return ("profit_positive",)
        if rate < 0:
            return ("profit_negative",)
        return ("profit_neutral",)

    def format_snapshot_summary(self, snapshot):
        unknown = snapshot.get("unknown_price_symbols", [])
        total_label = "可计算总持仓价值" if unknown else "总持仓价值"
        profit_label = "可计算总收益" if unknown else "总收益"
        summary = (
            f"{total_label}: ¥{snapshot.get('total_value', 0.0):.2f}    "
            f"{profit_label}: {snapshot.get('total_profit', 0.0):.2f} "
            f"({snapshot.get('total_profit_rate', 0.0):.2f}%)"
        )
        category_totals = snapshot.get("category_totals", {})
        parts = []
        for category in CATEGORY_OPTIONS:
            total = category_totals.get(category, {})
            if total.get("total_value", 0) or total.get("total_cost", 0):
                parts.append(f"{category}: {total.get('total_profit', 0.0):.2f}")
        if parts:
            summary += "    " + " / ".join(parts)
        if unknown:
            summary += f"    未计入: {', '.join(unknown)}"
        return summary

    def refresh_profit_chart(self):
        if self.chart_source_var.get() == "服务端价格记录":
            self.run_background(
                self.build_server_profit_chart_data,
                self.apply_server_profit_chart_data,
                "正在从服务端读取价格历史...",
            )
            return

        self.apply_profit_chart_data(self.build_profit_chart_data())
        self.status_var.set("已使用历史持仓结果更新收益走势")

    def apply_profit_chart_data(self, data):
        self.profit_chart_data = self.filter_profit_chart_data(data)
        if self.highlighted_series not in self.profit_chart_data["series"]:
            self.highlighted_series = None
        self.draw_profit_chart()

    def apply_server_profit_chart_data(self, data):
        self.apply_profit_chart_data(data)
        point_count = len(data.get("labels", []))
        series_count = len(data.get("series", {}))
        if point_count == 0 or series_count == 0:
            self.status_var.set("服务端价格历史读取成功，但暂无可绘制数据")
        else:
            self.status_var.set(
                f"服务端价格历史读取成功：{point_count} 个时间点，{series_count} 条曲线"
            )

    def series_value(self, profit, cost, metric):
        if metric == "收益率":
            return profit / cost * 100 if cost > 0 else 0.0
        return profit

    def build_profit_chart_data(self):
        snapshots = list(reversed(self.manager.list_holdings_snapshots()))
        metric = self.chart_metric_var.get()
        range_start = self.get_chart_range_start()
        labels = []
        series = {}
        meta = {}

        for _path, snapshot in snapshots:
            saved_at = snapshot.get("saved_at", "未知")
            if not self.is_chart_time_in_range(saved_at, range_start):
                continue

            point_index = len(labels)
            labels.append(saved_at)
            total_profit = float(snapshot.get("total_profit", 0.0))
            total_cost = float(snapshot.get("total_cost", 0.0))
            total_value = (
                float(snapshot.get("total_profit_rate", 0.0))
                if metric == "收益率"
                else total_profit
            )
            series.setdefault("总资产", []).append((point_index, total_value))
            meta["总资产"] = {"kind": "total"}

            category_totals = snapshot.get("category_totals", {})
            for category in CATEGORY_OPTIONS:
                totals = category_totals.get(category, {})
                profit = float(totals.get("total_profit", 0.0))
                cost = float(totals.get("total_cost", 0.0))
                if cost <= 0 and profit == 0:
                    continue
                key = f"{category}合计"
                series.setdefault(key, []).append((point_index, self.series_value(profit, cost, metric)))
                meta[key] = {"kind": "category", "category": category}

            if snapshot.get("assets"):
                for asset in snapshot.get("assets", []):
                    key = asset.get("label") or f"{asset.get('category', '')} {asset.get('symbol', '')}"
                    profit = float(asset.get("profit_cny", 0.0))
                    cost = float(asset.get("total_cost_cny", 0.0))
                    series.setdefault(key, []).append((point_index, self.series_value(profit, cost, metric)))
                    meta[key] = {"kind": "asset", "category": asset.get("category"), "asset_id": asset.get("asset_id")}
            else:
                for row in snapshot.get("rows", []):
                    if len(row) < 7:
                        continue
                    symbol = str(row[0])
                    try:
                        value = self.parse_metric_value(row[6 if metric == "收益率" else 5])
                    except ValueError:
                        continue
                    key = f"{CATEGORY_CRYPTO} {symbol}"
                    series.setdefault(key, []).append((point_index, value))
                    meta[key] = {"kind": "asset", "category": CATEGORY_CRYPTO}

        all_series = {
            key: points
            for key, points in sorted(series.items(), key=self.chart_series_sort_key)
            if points
        }
        return {
            "labels": labels,
            "series": all_series,
            "all_series": all_series,
            "series_meta": meta,
            "metric": metric,
            "source": "snapshots",
        }

    def build_server_profit_chart_data(self):
        range_start = self.get_chart_range_start()
        if hasattr(self.manager, "build_profit_history"):
            try:
                return self.manager.build_profit_history(
                    metric=self.chart_metric_var.get(),
                    start=range_start.strftime("%Y-%m-%d %H:%M:%S") if range_start else None,
                )
            except Exception:
                pass

        holdings = {
            asset["asset_id"]: asset
            for asset in self.manager.get_active_assets()
            if asset.get("quantity", 0) > 0 and asset.get("total_cost", 0) > 0
        }
        metric = self.chart_metric_var.get()
        if not holdings:
            return {"labels": [], "series": {}, "all_series": {}, "series_meta": {}, "metric": metric, "source": "server"}

        server_url = self.normalize_server_url()
        params = {
            "asset_ids": ",".join(sorted(holdings)),
            "limit": "5000" if range_start else "0",
            "full": "1",
        }
        if range_start:
            params["start"] = range_start.strftime("%Y-%m-%d %H:%M:%S")
        payload = self.fetch_server_json(server_url, "/api/assets/history", params)

        labels = []
        series = {}
        meta = {"总资产": {"kind": "total"}}
        for point in payload.get("points", []):
            timestamp = point.get("timestamp", "未知")
            if not self.is_chart_time_in_range(timestamp, range_start):
                continue

            price_cny = point.get("price_cny", {})
            fx_to_cny = point.get("fx_to_cny", {})
            point_index = len(labels)
            point_values = {}
            category_values = {
                category: {"profit": 0.0, "cost": 0.0, "has_value": False}
                for category in CATEGORY_OPTIONS
            }
            total_profit = 0.0
            total_cost = 0.0

            for asset_id, asset in holdings.items():
                price = price_cny.get(asset_id)
                if price is None:
                    continue
                value = asset["quantity"] * float(price)
                fx = fx_to_cny.get(asset_id)
                if fx is None:
                    if asset.get("currency", "").upper() == "CNY":
                        fx = 1.0
                    else:
                        continue
                cost = asset["total_cost"] * float(fx)
                profit = value - cost
                point_values[asset_id] = (profit, cost)
                total_profit += profit
                total_cost += cost
                category_bucket = category_values[asset["category"]]
                category_bucket["profit"] += profit
                category_bucket["cost"] += cost
                category_bucket["has_value"] = True

            if not point_values:
                continue

            labels.append(timestamp)
            series.setdefault("总资产", []).append((point_index, self.series_value(total_profit, total_cost, metric)))
            for category, bucket in category_values.items():
                if not bucket["has_value"]:
                    continue
                key = f"{category}合计"
                series.setdefault(key, []).append((point_index, self.series_value(bucket["profit"], bucket["cost"], metric)))
                meta[key] = {"kind": "category", "category": category}

            for asset_id, (profit, cost) in point_values.items():
                asset = holdings[asset_id]
                key = self.chart_asset_series_label(asset)
                series.setdefault(key, []).append((point_index, self.series_value(profit, cost, metric)))
                meta[key] = {"kind": "asset", "category": asset["category"], "asset_id": asset_id}

        all_series = {
            key: points
            for key, points in sorted(series.items(), key=self.chart_series_sort_key)
            if points
        }
        return {
            "labels": labels,
            "series": all_series,
            "all_series": all_series,
            "series_meta": meta,
            "metric": metric,
            "source": "server",
        }

    def chart_asset_series_label(self, asset):
        name = asset.get("name") or asset.get("symbol")
        if name and name != asset.get("symbol"):
            return f"{asset['category']} {asset['symbol']} {name}"
        return f"{asset['category']} {asset['symbol']}"

    def normalize_server_url(self):
        server_url = self.server_url_var.get().strip().rstrip("/")
        if not server_url:
            raise ValueError("服务端地址不能为空。")
        if "://" not in server_url:
            server_url = f"http://{server_url}"
        parsed = urlparse(server_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("服务端地址格式不正确，请使用 http://域名:端口。")
        return server_url

    def fetch_server_json(self, server_url, path, params):
        url = f"{server_url}{path}"
        session = requests.Session()
        session.trust_env = False
        last_error = None
        last_response = None
        try:
            for attempt in range(3):
                try:
                    response = session.get(url, params=params, timeout=10)
                    last_response = response
                    response.raise_for_status()
                    return response.json()
                except (requests.exceptions.RequestException, ValueError) as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    raise
        except requests.exceptions.Timeout as exc:
            raise ValueError(f"服务端请求超时: {url}") from exc
        except requests.exceptions.ConnectionError as exc:
            raise ValueError(f"无法连接服务端: {url}") from exc
        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "未知"
            raise ValueError(f"服务端返回错误状态 {status_code}: {url}") from exc
        except ValueError as exc:
            detail = ""
            response = getattr(last_error, "response", None) or last_response
            if response is not None:
                detail = f"，返回类型: {response.headers.get('content-type', '未知')}"
            raise ValueError(f"服务端返回内容不是有效 JSON{detail}: {url}") from exc
        finally:
            session.close()

    def chart_series_sort_key(self, item):
        key = item[0] if isinstance(item, tuple) else item
        if key == "总资产":
            return (0, key)
        for index, category in enumerate(CATEGORY_OPTIONS, start=1):
            if key == f"{category}合计":
                return (index, key)
        return (10, key)

    def filter_profit_chart_data(self, data):
        all_series = data.get("all_series", data.get("series", {}))
        self.chart_series_meta = data.get("series_meta", {})
        available = list(all_series.keys())
        self.update_chart_symbol_selector(available)
        selected = self.get_selected_chart_series(available)
        filtered_series = {
            key: points
            for key, points in all_series.items()
            if key in selected
        }
        filtered = data.copy()
        filtered["all_series"] = all_series
        filtered["series"] = filtered_series
        return filtered

    def chart_color_palette(self):
        return [
            "#c62828",
            "#1565c0",
            "#2e7d32",
            "#6a1b9a",
            "#ef6c00",
            "#00838f",
            "#ad1457",
            "#455a64",
            "#7b1fa2",
            "#5d4037",
        ]

    def get_chart_color_map(self, series_names):
        palette = self.chart_color_palette()
        sorted_names = sorted(series_names, key=self.chart_series_sort_key)
        return {
            name: palette[index % len(palette)]
            for index, name in enumerate(sorted_names)
        }

    def on_chart_symbol_canvas_configure(self, event):
        if hasattr(self, "chart_symbol_window"):
            self.chart_symbol_canvas.itemconfigure(self.chart_symbol_window, width=event.width)

    def on_chart_symbol_mousewheel(self, event):
        if not hasattr(self, "chart_symbol_canvas"):
            return
        self.chart_symbol_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def update_chart_symbol_selector(self, available_series):
        available_series = sorted(available_series, key=self.chart_series_sort_key)
        if not hasattr(self, "chart_symbol_list_frame"):
            return

        for child in self.chart_symbol_list_frame.winfo_children():
            child.destroy()

        if not available_series:
            ttk.Label(self.chart_symbol_list_frame, text="暂无曲线").pack(anchor="w")
            return

        for key in list(set(self.chart_series_vars) - set(available_series)):
            del self.chart_series_vars[key]

        has_existing_selection = any(var.get() for var in self.chart_series_vars.values())
        for key in available_series:
            if key not in self.chart_series_vars:
                self.chart_series_vars[key] = tk.BooleanVar(value=key == "总资产" and not has_existing_selection)

        if not any(self.chart_series_vars[key].get() for key in available_series):
            default_key = "总资产" if "总资产" in available_series else available_series[0]
            self.chart_series_vars[default_key].set(True)

        color_map = self.get_chart_color_map(available_series)
        for key in available_series:
            row = ttk.Frame(self.chart_symbol_list_frame)
            row.pack(fill="x", pady=2)
            row.bind("<MouseWheel>", self.on_chart_symbol_mousewheel)

            swatch = tk.Label(
                row,
                text="■",
                fg=color_map.get(key, "#333333"),
                width=2,
                cursor="hand2",
            )
            swatch.pack(side="left")
            swatch.bind("<Button-1>", lambda _event, name=key: self.toggle_chart_highlight(name))
            swatch.bind("<MouseWheel>", self.on_chart_symbol_mousewheel)

            label = f"{key} 选中" if self.highlighted_series == key else key
            checkbutton = ttk.Checkbutton(
                row,
                text=label,
                variable=self.chart_series_vars[key],
                command=self.on_chart_symbol_selection_changed,
            )
            checkbutton.pack(side="left", fill="x", expand=True)
            checkbutton.bind("<MouseWheel>", self.on_chart_symbol_mousewheel)

    def get_selected_chart_series(self, available_series):
        selected = [
            key for key in available_series
            if self.chart_series_vars.get(key) and self.chart_series_vars[key].get()
        ]
        if selected:
            return set(selected)
        if "总资产" in available_series:
            self.chart_series_vars["总资产"].set(True)
            return {"总资产"}
        if available_series:
            self.chart_series_vars[available_series[0]].set(True)
            return {available_series[0]}
        return set()

    def select_chart_group(self, group):
        if not self.chart_series_vars:
            self.refresh_profit_chart()
            return

        for var in self.chart_series_vars.values():
            var.set(False)

        if group == CATEGORY_ALL:
            if "总资产" in self.chart_series_vars:
                self.chart_series_vars["总资产"].set(True)
        else:
            key = f"{group}合计"
            if key in self.chart_series_vars:
                self.chart_series_vars[key].set(True)
            else:
                for name, meta in self.chart_series_meta.items():
                    if meta.get("category") == group:
                        self.chart_series_vars[name].set(True)

        self.on_chart_symbol_selection_changed()

    def on_chart_symbol_selection_changed(self):
        available = [
            key for key, var in self.chart_series_vars.items()
            if var is not None
        ]
        self.get_selected_chart_series(available)
        if self.profit_chart_data:
            self.profit_chart_data = self.filter_profit_chart_data(self.profit_chart_data)
            if self.highlighted_series not in self.profit_chart_data["series"]:
                self.highlighted_series = None
            self.draw_profit_chart()
        else:
            self.refresh_profit_chart()

    def get_chart_range_start(self):
        range_label = self.chart_range_var.get()
        now = datetime.now()
        ranges = {
            "过去一天": timedelta(days=1),
            "过去一周": timedelta(weeks=1),
            "过去一个月": timedelta(days=30),
            "过去半年": timedelta(days=183),
            "过去一年": timedelta(days=365),
        }
        delta = ranges.get(range_label)
        if delta is None:
            return None
        return now - delta

    def parse_chart_time(self, value):
        text = str(value).strip()
        formats = (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
        )
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
        return None

    def is_chart_time_in_range(self, value, range_start):
        if range_start is None:
            return True
        parsed = self.parse_chart_time(value)
        if parsed is None:
            return True
        return parsed >= range_start

    def draw_profit_chart(self):
        if not hasattr(self, "profit_chart_canvas"):
            return

        canvas = self.profit_chart_canvas
        canvas.delete("all")
        self.profit_chart_layout = None

        data = self.profit_chart_data or self.build_profit_chart_data()
        labels = data["labels"]
        series = data["series"]
        metric = data["metric"]

        width = max(canvas.winfo_width(), 760)
        height = max(canvas.winfo_height(), 420)
        if not labels or not series:
            empty_text = (
                "暂无可绘制的服务端价格记录。请先启动服务端并等待价格采集。"
                if data.get("source") == "server"
                else "暂无可绘制的历史持仓结果。请先在“持仓”页查询并保存。"
            )
            canvas.create_text(
                width / 2,
                height / 2,
                text=empty_text,
                fill="#666666",
                font=("Microsoft YaHei UI", 12),
            )
            return

        left = 58
        right = 50
        top = 42
        bottom = 76
        chart_left = left
        chart_right = width - right
        chart_top = top
        chart_bottom = height - bottom

        values = [value for points in series.values() for _index, value in points]
        min_value = min(values + [0])
        max_value = max(values + [0])
        if min_value == max_value:
            padding = abs(max_value) * 0.1 or 1
            min_value -= padding
            max_value += padding
        else:
            padding = (max_value - min_value) * 0.08
            min_value -= padding
            max_value += padding

        def x_for(index):
            if len(labels) == 1:
                return (chart_left + chart_right) / 2
            return chart_left + index / (len(labels) - 1) * (chart_right - chart_left)

        def y_for(value):
            return chart_bottom - (
                (value - min_value) / (max_value - min_value) * (chart_bottom - chart_top)
            )

        canvas.create_line(chart_left, chart_top, chart_left, chart_bottom, fill="#444444")
        canvas.create_line(chart_left, chart_bottom, chart_right, chart_bottom, fill="#444444")

        for step in range(6):
            value = min_value + (max_value - min_value) * step / 5
            y = y_for(value)
            canvas.create_line(chart_left, y, chart_right, y, fill="#eeeeee")
            canvas.create_text(
                chart_left - 8,
                y,
                text=self.format_chart_hover_value(value, metric),
                anchor="e",
                fill="#777777",
                font=("Microsoft YaHei UI", 8),
            )

        canvas.create_text(
            chart_left,
            18,
            text=(
                f"总资产与分类{metric}走势"
                + (f" - 已高亮 {self.highlighted_series}" if self.highlighted_series else "")
            ),
            anchor="w",
            fill="#222222",
            font=("Microsoft YaHei UI", 12, "bold"),
        )

        tick_count = min(6, len(labels))
        tick_indexes = sorted(
            {
                round(index * (len(labels) - 1) / max(tick_count - 1, 1))
                for index in range(tick_count)
            }
        )
        for index in tick_indexes:
            x = x_for(index)
            canvas.create_line(x, chart_bottom, x, chart_bottom + 5, fill="#444444")
            label = labels[index]
            if len(label) > 16:
                label = label[:16]
            canvas.create_text(x, chart_bottom + 24, text=label, anchor="n", fill="#555555")

        if min_value < 0 < max_value:
            y_zero = y_for(0)
            canvas.create_line(chart_left, y_zero, chart_right, y_zero, fill="#999999", dash=(4, 3))

        color_map = self.get_chart_color_map(data.get("all_series", series).keys())
        points_by_index = {index: [] for index in range(len(labels))}
        for name, points in series.items():
            selected = self.highlighted_series == name
            dimmed = self.highlighted_series is not None and not selected
            base_color = color_map.get(name, "#333333")
            color = "#cfcfcf" if dimmed else base_color
            line_width = 4 if selected else 2
            coords = []
            screen_points = []
            for index, value in points:
                x = x_for(index)
                y = y_for(value)
                coords.extend((x, y))
                screen_points.append({
                    "index": index,
                    "value": value,
                    "x": x,
                    "y": y,
                })
                points_by_index.setdefault(index, []).append({
                    "name": name,
                    "value": value,
                    "x": x,
                    "y": y,
                    "color": base_color,
                })
            if len(coords) >= 4:
                canvas.create_line(*coords, fill=color, width=line_width)
            self.draw_chart_extreme_labels(canvas, name, screen_points, color, metric, chart_right)

        self.profit_chart_layout = {
            "chart_left": chart_left,
            "chart_right": chart_right,
            "chart_top": chart_top,
            "chart_bottom": chart_bottom,
            "width": width,
            "height": height,
            "labels": labels,
            "metric": metric,
            "x_positions": [x_for(index) for index in range(len(labels))],
            "points_by_index": points_by_index,
        }

    def draw_chart_extreme_labels(self, canvas, name, screen_points, color, metric, chart_right):
        if not screen_points:
            return

        max_point = max(screen_points, key=lambda point: point["value"])
        min_point = min(screen_points, key=lambda point: point["value"])
        if max_point["index"] == min_point["index"] and max_point["value"] == min_point["value"]:
            labels = [("最高/最低", max_point)]
        else:
            labels = [("最高", max_point), ("最低", min_point)]

        for label_kind, point in labels:
            canvas.create_oval(
                point["x"] - 4,
                point["y"] - 4,
                point["x"] + 4,
                point["y"] + 4,
                fill="white",
                outline=color,
                width=2,
            )
            label = (
                f"{name} {label_kind} "
                f"{self.format_chart_hover_value(point['value'], metric)}"
            )
            anchor = "w"
            text_x = point["x"] + 8
            if text_x > chart_right - 150:
                anchor = "e"
                text_x = point["x"] - 8
            canvas.create_text(
                text_x,
                point["y"],
                text=label,
                anchor=anchor,
                fill=color,
                font=("Microsoft YaHei UI", 8, "bold"),
            )

    def on_profit_chart_motion(self, event):
        layout = self.profit_chart_layout
        if not layout:
            return

        if event.x < layout["chart_left"] or event.x > layout["chart_right"]:
            self.clear_chart_hover()
            return

        x_positions = layout["x_positions"]
        if not x_positions:
            self.clear_chart_hover()
            return

        nearest_index = min(
            range(len(x_positions)),
            key=lambda index: abs(x_positions[index] - event.x),
        )
        points = layout["points_by_index"].get(nearest_index, [])
        if not points:
            self.clear_chart_hover()
            return

        self.draw_chart_hover(nearest_index, event.x, event.y)

    def draw_chart_hover(self, index, mouse_x, mouse_y):
        canvas = self.profit_chart_canvas
        layout = self.profit_chart_layout
        canvas.delete("chart_hover")

        x = layout["x_positions"][index]
        points = list(layout["points_by_index"].get(index, []))
        if not points:
            return

        points.sort(key=self.chart_hover_sort_key)
        metric = layout["metric"]
        rows = [
            f"{point['name']}: {self.format_chart_hover_value(point['value'], metric)}"
            for point in points
        ]
        title = layout["labels"][index]

        line_height = 20
        padding_x = 10
        padding_y = 8
        box_width = max([len(title) * 8] + [len(row) * 8 for row in rows]) + padding_x * 2
        box_width = min(box_width, 420)
        box_height = padding_y * 2 + line_height * (len(rows) + 1)

        box_x = mouse_x + 16
        if box_x + box_width > layout["width"] - 8:
            box_x = mouse_x - box_width - 16
        box_x = max(8, min(box_x, layout["width"] - box_width - 8))

        box_y = mouse_y + 16
        if box_y + box_height > layout["height"] - 8:
            box_y = mouse_y - box_height - 16
        box_y = max(8, min(box_y, layout["height"] - box_height - 8))

        canvas.create_line(
            x,
            layout["chart_top"],
            x,
            layout["chart_bottom"],
            fill="#777777",
            dash=(4, 3),
            tags=("chart_hover",),
        )
        canvas.create_rectangle(
            box_x + 3,
            box_y + 3,
            box_x + box_width + 3,
            box_y + box_height + 3,
            fill="#d7d7d7",
            outline="",
            tags=("chart_hover",),
        )
        canvas.create_rectangle(
            box_x,
            box_y,
            box_x + box_width,
            box_y + box_height,
            fill="#ffffff",
            outline="#888888",
            tags=("chart_hover",),
        )
        canvas.create_text(
            box_x + padding_x,
            box_y + padding_y,
            text=title,
            anchor="nw",
            fill="#222222",
            font=("Microsoft YaHei UI", 9, "bold"),
            tags=("chart_hover",),
        )

        for row_index, point in enumerate(points, start=1):
            y = box_y + padding_y + line_height * row_index
            selected = self.highlighted_series == point["name"]
            canvas.create_text(
                box_x + padding_x,
                y,
                text=f"{point['name']}: {self.format_chart_hover_value(point['value'], metric)}",
                anchor="nw",
                fill=point["color"],
                font=("Microsoft YaHei UI", 9, "bold") if selected else ("Microsoft YaHei UI", 9),
                tags=("chart_hover",),
            )

    def chart_hover_sort_key(self, point):
        if self.highlighted_series == point["name"]:
            return (0, point["name"])
        return (1, point["name"])

    def format_chart_hover_value(self, value, metric):
        if metric == "收益率":
            return f"{value:.2f}%"
        return f"{value:.2f}"

    def clear_chart_hover(self):
        if hasattr(self, "profit_chart_canvas"):
            self.profit_chart_canvas.delete("chart_hover")

    def toggle_chart_highlight(self, name):
        if name in self.chart_series_vars and not self.chart_series_vars[name].get():
            self.chart_series_vars[name].set(True)

        if self.highlighted_series == name:
            self.highlighted_series = None
        else:
            self.highlighted_series = name

        if self.profit_chart_data:
            self.profit_chart_data = self.filter_profit_chart_data(self.profit_chart_data)
            if self.highlighted_series not in self.profit_chart_data["series"]:
                self.highlighted_series = None
        self.draw_profit_chart()

    def clear_chart_highlight(self):
        self.highlighted_series = None
        if self.profit_chart_data:
            self.profit_chart_data = self.filter_profit_chart_data(self.profit_chart_data)
        self.draw_profit_chart()

    def parse_trade_form(self):
        selected_asset = self.selected_trade_asset()
        category = normalize_category(selected_asset.get("category"))
        market = normalize_market(selected_asset.get("market"), category)
        symbol = selected_asset.get("symbol", "").strip().upper()
        name = selected_asset.get("name", "").strip()
        tx_type = "buy" if self.tx_type_var.get() == "买入" else "sell"
        try:
            amount = float(self.amount_var.get().strip())
            price = float(self.price_var.get().strip())
        except ValueError:
            messagebox.showerror("输入错误", "数量和价格必须是有效数字。")
            return None

        date = self.date_var.get().strip()
        return category, market, symbol, name, tx_type, amount, price, date

    def add_transaction(self):
        parsed = self.parse_trade_form()
        if parsed is None:
            return

        category, market, symbol, name, tx_type, amount, price, date = parsed
        if tx_type == "buy":
            saved = self.manager.buy_asset(category, market, symbol, amount, price, date, name)
        else:
            asset_id = self.manager.find_asset_id(symbol, category, market)
            saved = self.manager.sell_asset(asset_id, amount, price, date)

        if saved:
            self.after_data_change("交易已新增")
        else:
            messagebox.showwarning("未保存", "交易没有保存，请检查输入和持仓。")

    def update_transaction(self):
        if self.selected_transaction is None:
            messagebox.showinfo("提示", "请先选择一条交易记录。")
            return

        parsed = self.parse_trade_form()
        if parsed is None:
            return

        asset_id, old_index = self.selected_transaction
        asset = self.manager.data.get(asset_id)
        if not asset:
            messagebox.showwarning("提示", "选中的资产不存在。")
            return

        category, market, symbol, _name, tx_type, amount, price, date = parsed
        if (
            normalize_category(category) != asset["category"]
            or normalize_market(market, category) != asset["market"]
            or symbol.strip().upper() != asset["symbol"]
        ):
            messagebox.showwarning("暂不支持", "编辑时不能修改类别、市场或代码。如需更换资产，请删除后重新新增。")
            return

        if self.manager.update_transaction_by_asset(asset_id, old_index, tx_type, amount, price, date):
            self.after_data_change("交易已修改")
        else:
            messagebox.showwarning("未保存", "修改失败，请检查输入和后续卖出记录。")

    def delete_selected_transaction(self):
        if self.selected_transaction is None:
            messagebox.showinfo("提示", "请先选择一条交易记录。")
            return

        if not messagebox.askyesno("确认删除", "确认删除选中的交易记录？"):
            return

        asset_id, index = self.selected_transaction
        if self.manager.delete_transaction_by_asset(asset_id, index):
            self.clear_transaction_form()
            self.after_data_change("交易已删除")
        else:
            messagebox.showwarning("未删除", "删除失败，删除后账单可能会导致卖出数量超过持仓。")

    def on_transaction_select(self, _event):
        selection = self.transactions_tree.selection()
        if not selection:
            return

        values = self.transactions_tree.item(selection[0], "values")
        (
            category, market_label, symbol, name, index, tx_type, date,
            amount, price, currency, _total, asset_id,
        ) = values
        self.selected_transaction = (asset_id, int(index))
        self.category_var.set(category)
        self.update_form_market_options()
        self.market_var.set(market_label)
        asset = self.manager.data.get(asset_id, {
            "asset_id": asset_id,
            "category": category,
            "market": self.current_form_market(),
            "symbol": symbol,
            "name": name,
            "currency": currency,
        })
        display = suggestion_label(asset)
        self.symbol_display_to_asset[display] = asset
        self.symbol_display_to_asset[symbol] = asset
        self.symbol_var.set(display)
        self.name_var.set(name)
        self.tx_type_var.set(tx_type)
        self.date_var.set(date)
        self.amount_var.set(amount)
        self.price_var.set(price)
        self.currency_var.set(currency)
        self.update_trade_amount_labels()

    def clear_transaction_form(self):
        self.selected_transaction = None
        self.category_var.set(CATEGORY_CRYPTO)
        self.update_form_market_options()
        self.symbol_var.set("")
        self.name_var.set("")
        self.tx_type_var.set("买入")
        self.amount_var.set("")
        self.price_var.set("")
        self.date_var.set(self.manager.now())
        self.transactions_tree.selection_remove(self.transactions_tree.selection())

    def after_data_change(self, message):
        self.refresh_assets()
        self.refresh_symbols()
        self.refresh_transactions()
        self.tx_summary_var.set("持仓已变化，点击“刷新收益”查看当前总资产收益")
        self.profit_chart_data = None
        self.status_var.set(message)

    def snapshot_rows_for_display(self, snapshot):
        rows = snapshot.get("rows", [])
        if not rows:
            return []
        if len(rows[0]) >= 13:
            return rows

        converted = []
        for row in rows:
            if len(row) < 7:
                continue
            converted.append([
                CATEGORY_CRYPTO,
                MARKET_LABELS[MARKET_CRYPTO],
                row[0],
                row[0],
                row[1],
                row[2],
                row[3],
                "USD",
                "",
                row[4],
                "",
                row[5],
                row[6],
            ])
        return converted

    def on_snapshot_select(self, _event):
        selection = self.snapshots_tree.selection()
        if not selection:
            return

        values = self.snapshots_tree.item(selection[0], "values")
        path = Path(values[3])
        try:
            snapshots = dict(self.manager.list_holdings_snapshots())
            snapshot = snapshots[path]
        except KeyError:
            messagebox.showwarning("提示", "该快照文件不存在。")
            return

        self.fill_tree(self.snapshot_detail_tree, self.snapshot_rows_for_display(snapshot), self.rate_tag_for_row)
        self.snapshot_summary_var.set(self.format_snapshot_summary(snapshot))

    def delete_selected_snapshot(self):
        selection = self.snapshots_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个历史持仓结果。")
            return

        values = self.snapshots_tree.item(selection[0], "values")
        path = Path(values[3])
        if not messagebox.askyesno("确认删除", "确认删除选中的历史持仓结果？"):
            return

        try:
            path.unlink()
        except OSError as exc:
            messagebox.showerror("删除失败", str(exc))
            return

        self.refresh_snapshots()
        self.fill_tree(self.snapshot_detail_tree, [])
        self.snapshot_summary_var.set("")
        self.status_var.set("历史持仓结果已删除")


def main():
    app = PortfolioApp()
    app.mainloop()
