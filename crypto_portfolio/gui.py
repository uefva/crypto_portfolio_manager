from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import requests

from crypto_portfolio.portfolio_manager import (
    COIN_MAP,
    HOLDING_SNAPSHOT_DIR,
    PortfolioManager,
)


class PortfolioApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("加密货币持仓管理")
        self.geometry("1180x720")
        self.minsize(980, 620)

        self.manager = PortfolioManager()
        self.selected_transaction = None

        self.status_var = tk.StringVar(value="就绪")
        self.holding_summary_var = tk.StringVar(value="")
        self.snapshot_summary_var = tk.StringVar(value="")
        self.chart_source_var = tk.StringVar(value="历史仓位结果")
        self.chart_metric_var = tk.StringVar(value="收益金额")
        self.chart_range_var = tk.StringVar(value="全部时间")
        self.server_url_var = tk.StringVar(value="http://687pq84al732.vicp.fun:47649")
        self.profit_chart_data = None
        self.profit_chart_layout = None
        self.highlighted_symbol = None
        self.chart_symbol_vars = {}

        self.create_widgets()
        self.refresh_all()

    def create_widgets(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        self.holdings_tab = ttk.Frame(self.notebook)
        self.transactions_tab = ttk.Frame(self.notebook)
        self.snapshots_tab = ttk.Frame(self.notebook)
        self.profit_chart_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.holdings_tab, text="持仓")
        self.notebook.add(self.transactions_tab, text="交易记录")
        self.notebook.add(self.snapshots_tab, text="历史持仓结果")
        self.notebook.add(self.profit_chart_tab, text="收益走势")

        self.create_holdings_tab()
        self.create_transactions_tab()
        self.create_snapshots_tab()
        self.create_profit_chart_tab()

        status = ttk.Label(self, textvariable=self.status_var, anchor="w")
        status.pack(fill="x", padx=10, pady=8)

    def create_holdings_tab(self):
        toolbar = ttk.Frame(self.holdings_tab)
        toolbar.pack(fill="x", pady=(0, 8))

        self.refresh_holdings_button = ttk.Button(
            toolbar,
            text="查询并保存",
            command=self.refresh_holdings,
        )
        self.refresh_holdings_button.pack(side="left")
        ttk.Button(toolbar, text="刷新本地数据", command=self.refresh_all).pack(side="left", padx=8)

        columns = ("symbol", "quantity", "avg_cost", "price", "value", "profit", "rate")
        self.holdings_tree = ttk.Treeview(
            self.holdings_tab,
            columns=columns,
            show="headings",
            height=18,
        )
        headings = {
            "symbol": "币种",
            "quantity": "数量",
            "avg_cost": "成本价",
            "price": "当前价",
            "value": "持仓价值",
            "profit": "总收益",
            "rate": "收益率",
        }
        widths = {
            "symbol": 90,
            "quantity": 150,
            "avg_cost": 130,
            "price": 130,
            "value": 130,
            "profit": 130,
            "rate": 110,
        }
        for column in columns:
            anchor = "w" if column == "symbol" else "e"
            self.holdings_tree.heading(column, text=headings[column])
            self.holdings_tree.column(column, width=widths[column], anchor=anchor)
        self.configure_rate_tags(self.holdings_tree)
        self.configure_sortable_tree(self.holdings_tree, headings)

        self.holdings_tree.pack(fill="both", expand=True)
        ttk.Label(self.holdings_tab, textvariable=self.holding_summary_var, anchor="w").pack(
            fill="x", pady=(8, 0)
        )

    def create_transactions_tab(self):
        form = ttk.LabelFrame(self.transactions_tab, text="新增 / 编辑交易")
        form.pack(fill="x", pady=(0, 8))

        self.symbol_var = tk.StringVar()
        self.tx_type_var = tk.StringVar(value="买入")
        self.amount_var = tk.StringVar()
        self.price_var = tk.StringVar()
        self.date_var = tk.StringVar(value=self.manager.now())

        ttk.Label(form, text="币种").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        self.symbol_combo = ttk.Combobox(form, textvariable=self.symbol_var, width=14)
        self.symbol_combo.grid(row=0, column=1, padx=8, pady=8, sticky="we")

        ttk.Label(form, text="类型").grid(row=0, column=2, padx=8, pady=8, sticky="w")
        ttk.Combobox(
            form,
            textvariable=self.tx_type_var,
            values=("买入", "卖出"),
            width=10,
            state="readonly",
        ).grid(row=0, column=3, padx=8, pady=8, sticky="we")

        ttk.Label(form, text="数量").grid(row=0, column=4, padx=8, pady=8, sticky="w")
        ttk.Entry(form, textvariable=self.amount_var, width=16).grid(
            row=0, column=5, padx=8, pady=8, sticky="we"
        )

        ttk.Label(form, text="价格(USD)").grid(row=1, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(form, textvariable=self.price_var, width=16).grid(
            row=1, column=1, padx=8, pady=8, sticky="we"
        )

        ttk.Label(form, text="日期").grid(row=1, column=2, padx=8, pady=8, sticky="w")
        ttk.Entry(form, textvariable=self.date_var, width=24).grid(
            row=1, column=3, padx=8, pady=8, sticky="we"
        )

        buttons = ttk.Frame(form)
        buttons.grid(row=1, column=4, columnspan=2, padx=8, pady=8, sticky="e")
        ttk.Button(buttons, text="新增", command=self.add_transaction).pack(side="left")
        ttk.Button(buttons, text="保存修改", command=self.update_transaction).pack(side="left", padx=8)
        ttk.Button(buttons, text="删除选中", command=self.delete_selected_transaction).pack(side="left")
        ttk.Button(buttons, text="清空", command=self.clear_transaction_form).pack(side="left", padx=(8, 0))

        for column in range(6):
            form.columnconfigure(column, weight=1)

        columns = ("symbol", "index", "type", "date", "amount", "price", "total")
        self.transactions_tree = ttk.Treeview(
            self.transactions_tab,
            columns=columns,
            show="headings",
            height=18,
        )
        headings = {
            "symbol": "币种",
            "index": "序号",
            "type": "类型",
            "date": "日期",
            "amount": "数量",
            "price": "价格",
            "total": "总金额",
        }
        widths = {
            "symbol": 80,
            "index": 70,
            "type": 80,
            "date": 170,
            "amount": 140,
            "price": 130,
            "total": 130,
        }
        for column in columns:
            anchor = "w" if column in {"symbol", "type", "date"} else "e"
            self.transactions_tree.heading(column, text=headings[column])
            self.transactions_tree.column(column, width=widths[column], anchor=anchor)
        self.configure_sortable_tree(self.transactions_tree, headings)

        self.transactions_tree.pack(fill="both", expand=True)
        self.transactions_tree.bind("<<TreeviewSelect>>", self.on_transaction_select)

    def create_snapshots_tab(self):
        outer = ttk.PanedWindow(self.snapshots_tab, orient="horizontal")
        outer.pack(fill="both", expand=True)

        left = ttk.Frame(outer)
        right = ttk.Frame(outer)
        outer.add(left, weight=1)
        outer.add(right, weight=2)

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
            ("value", "总价值", 100),
            ("profit", "总收益", 100),
            ("path", "文件", 220),
        ):
            snapshot_headings[column] = title
            self.snapshots_tree.heading(column, text=title)
            self.snapshots_tree.column(column, width=width, anchor="w")
        self.configure_sortable_tree(self.snapshots_tree, snapshot_headings)
        self.snapshots_tree.pack(fill="both", expand=True)
        self.snapshots_tree.bind("<<TreeviewSelect>>", self.on_snapshot_select)

        self.snapshot_detail_tree = ttk.Treeview(
            right,
            columns=("symbol", "quantity", "avg_cost", "price", "value", "profit", "rate"),
            show="headings",
        )
        detail_headings = {}
        for column, title, width in (
            ("symbol", "币种", 80),
            ("quantity", "数量", 130),
            ("avg_cost", "成本价", 120),
            ("price", "当前价", 120),
            ("value", "持仓价值", 120),
            ("profit", "总收益", 120),
            ("rate", "收益率", 100),
        ):
            detail_headings[column] = title
            anchor = "w" if column == "symbol" else "e"
            self.snapshot_detail_tree.heading(column, text=title)
            self.snapshot_detail_tree.column(column, width=width, anchor=anchor)
        self.configure_rate_tags(self.snapshot_detail_tree)
        self.configure_sortable_tree(self.snapshot_detail_tree, detail_headings)
        self.snapshot_detail_tree.pack(fill="both", expand=True)
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
        ttk.Button(toolbar, text="清除高亮", command=self.clear_chart_highlight).pack(
            side="left", padx=8
        )

        chart_body = ttk.Frame(self.profit_chart_tab)
        chart_body.pack(fill="both", expand=True)

        self.chart_symbol_panel = ttk.LabelFrame(chart_body, text="展示曲线", padding=(8, 6))
        self.chart_symbol_panel.pack(side="right", fill="y", padx=(8, 0))
        self.chart_symbol_panel.pack_propagate(False)
        self.chart_symbol_panel.configure(width=150)
        self.chart_symbol_canvas = tk.Canvas(
            self.chart_symbol_panel,
            highlightthickness=0,
            width=126,
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
        self.manager.data = self.manager.load_data()
        self.refresh_symbols()
        self.refresh_transactions()
        self.refresh_snapshots()
        self.status_var.set("本地数据已刷新")

    def refresh_symbols(self):
        symbols = []
        for symbol in self.manager.get_symbols() + sorted(COIN_MAP.keys()):
            if symbol not in symbols:
                symbols.append(symbol)
        self.symbol_combo["values"] = symbols

    def refresh_holdings(self):
        if not self.manager.data:
            messagebox.showinfo("提示", "暂无持仓。")
            return

        self.refresh_holdings_button.configure(state="disabled")

        def task():
            prices = self.manager.get_prices()
            snapshot = self.manager.build_holdings_snapshot(prices)
            snapshot_path = self.manager.save_holdings_snapshot(snapshot)
            return snapshot, snapshot_path

        def on_success(result):
            snapshot, snapshot_path = result
            self.fill_tree(self.holdings_tree, snapshot["rows"], self.rate_tag_for_row)
            self.holding_summary_var.set(self.format_snapshot_summary(snapshot))
            self.refresh_snapshots()
            self.status_var.set(f"持仓查询已保存: {snapshot_path}")

        def on_done():
            self.refresh_holdings_button.configure(state="normal")

        self.run_background(task, on_success, "正在查询价格...", on_done=on_done)

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
        transactions = self.manager.get_transactions()
        rows = []
        for tx in transactions:
            rows.append((
                tx["symbol"],
                tx["index"],
                "买入" if tx["type"] == "buy" else "卖出",
                tx["date"],
                tx["amount"],
                tx["price"],
                tx["total"],
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
        text = str(value).strip().replace(",", "").replace("$", "")
        if text.endswith("%"):
            text = text[:-1]
        return float(text)

    def configure_rate_tags(self, tree):
        tree.tag_configure("profit_positive", foreground="#c62828")
        tree.tag_configure("profit_negative", foreground="#2e7d32")
        tree.tag_configure("profit_neutral", foreground="#555555")
        tree.tag_configure("profit_unknown", foreground="#777777")

    def rate_tag_for_row(self, row):
        if len(row) < 7:
            return ()

        rate_text = str(row[6]).strip()
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
            f"{total_label}: ${snapshot['total_value']:.2f}    "
            f"{profit_label}: {snapshot['total_profit']:.2f} "
            f"({snapshot['total_profit_rate']:.2f}%)"
        )
        if unknown:
            summary += f"    未计入: {', '.join(unknown)}"
        return summary

    def refresh_profit_chart(self):
        if self.chart_source_var.get() == "服务端价格记录":
            self.run_background(
                self.build_server_profit_chart_data,
                self.apply_profit_chart_data,
                "正在从服务端读取价格历史...",
            )
            return

        self.apply_profit_chart_data(self.build_profit_chart_data())

    def apply_profit_chart_data(self, data):
        self.profit_chart_data = self.filter_profit_chart_data(data)
        if self.highlighted_symbol not in self.profit_chart_data["series"]:
            self.highlighted_symbol = None
        self.draw_profit_chart()

    def build_profit_chart_data(self):
        snapshots = list(reversed(self.manager.list_holdings_snapshots()))
        metric = self.chart_metric_var.get()
        value_index = 6 if metric == "收益率" else 5
        range_start = self.get_chart_range_start()
        labels = []
        series = {}

        for _path, snapshot in snapshots:
            saved_at = snapshot.get("saved_at", "未知")
            if not self.is_chart_time_in_range(saved_at, range_start):
                continue

            point_index = len(labels)
            labels.append(saved_at)

            total_value = (
                snapshot.get("total_profit_rate", 0.0)
                if metric == "收益率"
                else snapshot.get("total_profit", 0.0)
            )
            series.setdefault("总收益", []).append((point_index, float(total_value)))

            for row in snapshot.get("rows", []):
                if len(row) <= value_index:
                    continue
                symbol = str(row[0])
                try:
                    value = self.parse_metric_value(row[value_index])
                except ValueError:
                    continue
                series.setdefault(symbol, []).append((point_index, value))

        all_series = {
            symbol: points
            for symbol, points in sorted(series.items(), key=self.chart_symbol_sort_key)
            if points
        }
        return {
            "labels": labels,
            "series": all_series,
            "all_series": all_series,
            "metric": metric,
            "source": "snapshots",
        }

    def build_server_profit_chart_data(self):
        holdings = {
            symbol: asset.copy()
            for symbol, asset in self.manager.data.items()
            if asset.get("quantity", 0) > 0 and asset.get("total_cost", 0) > 0
        }
        metric = self.chart_metric_var.get()
        if not holdings:
            return {"labels": [], "series": {}, "metric": metric, "source": "server"}

        server_url = self.normalize_server_url()

        params = {
            "symbols": ",".join(sorted(holdings.keys())),
            "limit": "5000",
        }
        range_start = self.get_chart_range_start()
        if range_start:
            params["start"] = range_start.strftime("%Y-%m-%d %H:%M:%S")
        payload = self.fetch_server_json(server_url, "/api/prices/history", params)

        labels = []
        series = {}
        for point in payload.get("points", []):
            prices = point.get("prices", {})
            timestamp = point.get("timestamp", "未知")
            if not self.is_chart_time_in_range(timestamp, range_start):
                continue

            point_values = {}
            total_profit = 0.0
            total_cost = 0.0

            for symbol, asset in holdings.items():
                price = prices.get(symbol)
                if price is None:
                    continue

                profit = asset["quantity"] * float(price) - asset["total_cost"]
                if metric == "收益率":
                    value = profit / asset["total_cost"] * 100
                else:
                    value = profit
                point_values[symbol] = value
                total_profit += profit
                total_cost += asset["total_cost"]

            if not point_values:
                continue

            point_index = len(labels)
            labels.append(timestamp)
            if metric == "收益率":
                total_value = total_profit / total_cost * 100 if total_cost > 0 else 0.0
            else:
                total_value = total_profit
            series.setdefault("总收益", []).append((point_index, total_value))
            for symbol, value in point_values.items():
                series.setdefault(symbol, []).append((point_index, value))

        all_series = {
            symbol: points
            for symbol, points in sorted(series.items(), key=self.chart_symbol_sort_key)
            if points
        }
        return {
            "labels": labels,
            "series": all_series,
            "all_series": all_series,
            "metric": metric,
            "source": "server",
        }

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

    def chart_symbol_sort_key(self, item):
        symbol = item[0] if isinstance(item, tuple) else item
        if symbol == "总收益":
            return (0, symbol)
        return (1, symbol)

    def filter_profit_chart_data(self, data):
        all_series = data.get("all_series", data.get("series", {}))
        available_symbols = list(all_series.keys())
        self.update_chart_symbol_selector(available_symbols)

        selected_symbols = self.get_selected_chart_symbols(available_symbols)
        filtered_series = {
            symbol: points
            for symbol, points in all_series.items()
            if symbol in selected_symbols
        }

        filtered_data = data.copy()
        filtered_data["all_series"] = all_series
        filtered_data["series"] = filtered_series
        return filtered_data

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

    def get_chart_color_map(self, symbols):
        palette = self.chart_color_palette()
        sorted_symbols = sorted(symbols, key=self.chart_symbol_sort_key)
        return {
            symbol: palette[index % len(palette)]
            for index, symbol in enumerate(sorted_symbols)
        }

    def on_chart_symbol_canvas_configure(self, event):
        if hasattr(self, "chart_symbol_window"):
            self.chart_symbol_canvas.itemconfigure(self.chart_symbol_window, width=event.width)

    def on_chart_symbol_mousewheel(self, event):
        if not hasattr(self, "chart_symbol_canvas"):
            return
        self.chart_symbol_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def update_chart_symbol_selector(self, available_symbols):
        available_symbols = sorted(available_symbols, key=self.chart_symbol_sort_key)
        if not hasattr(self, "chart_symbol_list_frame"):
            return

        for child in self.chart_symbol_list_frame.winfo_children():
            child.destroy()

        if not available_symbols:
            ttk.Label(self.chart_symbol_list_frame, text="暂无曲线").pack(anchor="w")
            return

        existing_symbols = set(self.chart_symbol_vars)
        for symbol in list(existing_symbols - set(available_symbols)):
            del self.chart_symbol_vars[symbol]

        has_existing_selection = any(var.get() for var in self.chart_symbol_vars.values())
        for symbol in available_symbols:
            if symbol not in self.chart_symbol_vars:
                default_selected = symbol == "总收益" and not has_existing_selection
                self.chart_symbol_vars[symbol] = tk.BooleanVar(value=default_selected)

        if not any(self.chart_symbol_vars[symbol].get() for symbol in available_symbols):
            default_symbol = "总收益" if "总收益" in available_symbols else available_symbols[0]
            self.chart_symbol_vars[default_symbol].set(True)

        color_map = self.get_chart_color_map(available_symbols)
        for symbol in available_symbols:
            row = ttk.Frame(self.chart_symbol_list_frame)
            row.pack(fill="x", pady=2)
            row.bind("<MouseWheel>", self.on_chart_symbol_mousewheel)

            swatch = tk.Label(
                row,
                text="■",
                fg=color_map.get(symbol, "#333333"),
                width=2,
                cursor="hand2",
            )
            swatch.pack(side="left")
            swatch.bind("<Button-1>", lambda _event, sym=symbol: self.toggle_chart_highlight(sym))
            swatch.bind("<MouseWheel>", self.on_chart_symbol_mousewheel)

            label = f"{symbol} 选中" if self.highlighted_symbol == symbol else symbol
            checkbutton = ttk.Checkbutton(
                row,
                text=label,
                variable=self.chart_symbol_vars[symbol],
                command=self.on_chart_symbol_selection_changed,
            )
            checkbutton.pack(side="left", fill="x", expand=True)
            checkbutton.bind("<MouseWheel>", self.on_chart_symbol_mousewheel)

    def get_selected_chart_symbols(self, available_symbols):
        selected = [
            symbol for symbol in available_symbols
            if self.chart_symbol_vars.get(symbol) and self.chart_symbol_vars[symbol].get()
        ]
        if selected:
            return set(selected)

        if "总收益" in available_symbols:
            self.chart_symbol_vars["总收益"].set(True)
            return {"总收益"}
        if available_symbols:
            self.chart_symbol_vars[available_symbols[0]].set(True)
            return {available_symbols[0]}
        return set()

    def on_chart_symbol_selection_changed(self):
        available_symbols = [
            symbol for symbol, var in self.chart_symbol_vars.items()
            if var is not None
        ]
        self.get_selected_chart_symbols(available_symbols)
        if self.profit_chart_data:
            self.profit_chart_data = self.filter_profit_chart_data(self.profit_chart_data)
            if self.highlighted_symbol not in self.profit_chart_data["series"]:
                self.highlighted_symbol = None
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
            if data.get("source") == "server":
                empty_text = "暂无可绘制的服务端价格记录。请先启动服务端并等待价格采集。"
            else:
                empty_text = "暂无可绘制的历史持仓结果。请先在“持仓”页查询并保存。"
            canvas.create_text(
                width / 2,
                height / 2,
                text=empty_text,
                fill="#666666",
                font=("Microsoft YaHei UI", 12),
            )
            return

        left = 48
        right = 48
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
            chart_left,
            18,
            text=(
                f"不同币种{metric}走势"
                + (f" - 已高亮 {self.highlighted_symbol}" if self.highlighted_symbol else "")
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
        for symbol, points in series.items():
            selected = self.highlighted_symbol == symbol
            dimmed = self.highlighted_symbol is not None and not selected
            base_color = color_map.get(symbol, "#333333")
            color = "#cfcfcf" if dimmed else base_color
            line_width = 4 if selected else 2
            marker_radius = 5 if selected else 3
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
                    "symbol": symbol,
                    "value": value,
                    "x": x,
                    "y": y,
                    "color": base_color,
                })
                canvas.create_oval(
                    x - marker_radius,
                    y - marker_radius,
                    x + marker_radius,
                    y + marker_radius,
                    fill=color,
                    outline=color,
                )
            if len(coords) >= 4:
                canvas.create_line(*coords, fill=color, width=line_width)
            self.draw_chart_extreme_labels(canvas, symbol, screen_points, color, metric, chart_right)

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

    def draw_chart_extreme_labels(self, canvas, symbol, screen_points, color, metric, chart_right):
        if not screen_points:
            return

        max_point = max(screen_points, key=lambda point: point["value"])
        min_point = min(screen_points, key=lambda point: point["value"])
        if max_point["index"] == min_point["index"] and max_point["value"] == min_point["value"]:
            labels = [("最高/最低", max_point)]
        else:
            labels = [("最高", max_point), ("最低", min_point)]

        for label_kind, point in labels:
            label = (
                f"{symbol} {label_kind} "
                f"{self.format_chart_hover_value(point['value'], metric)}"
            )
            anchor = "w"
            text_x = point["x"] + 8
            if text_x > chart_right - 120:
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
            f"{point['symbol']}: {self.format_chart_hover_value(point['value'], metric)}"
            for point in points
        ]
        title = layout["labels"][index]

        line_height = 20
        padding_x = 10
        padding_y = 8
        box_width = max([len(title) * 8] + [len(row) * 8 for row in rows]) + padding_x * 2
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
            selected = self.highlighted_symbol == point["symbol"]
            canvas.create_text(
                box_x + padding_x,
                y,
                text=f"{point['symbol']}: {self.format_chart_hover_value(point['value'], metric)}",
                anchor="nw",
                fill=point["color"],
                font=("Microsoft YaHei UI", 9, "bold") if selected else ("Microsoft YaHei UI", 9),
                tags=("chart_hover",),
            )
            canvas.create_oval(
                point["x"] - 5,
                point["y"] - 5,
                point["x"] + 5,
                point["y"] + 5,
                outline=point["color"],
                width=2,
                tags=("chart_hover",),
            )

    def chart_hover_sort_key(self, point):
        if self.highlighted_symbol == point["symbol"]:
            return (0, point["symbol"])
        return (1, point["symbol"])

    def format_chart_hover_value(self, value, metric):
        if metric == "收益率":
            return f"{value:.2f}%"
        return f"{value:.2f}"

    def clear_chart_hover(self):
        if hasattr(self, "profit_chart_canvas"):
            self.profit_chart_canvas.delete("chart_hover")

    def toggle_chart_highlight(self, symbol):
        if symbol in self.chart_symbol_vars and not self.chart_symbol_vars[symbol].get():
            self.chart_symbol_vars[symbol].set(True)

        if self.highlighted_symbol == symbol:
            self.highlighted_symbol = None
        else:
            self.highlighted_symbol = symbol

        if self.profit_chart_data:
            self.profit_chart_data = self.filter_profit_chart_data(self.profit_chart_data)
            if self.highlighted_symbol not in self.profit_chart_data["series"]:
                self.highlighted_symbol = None
        self.draw_profit_chart()

    def clear_chart_highlight(self):
        self.highlighted_symbol = None
        if self.profit_chart_data:
            self.profit_chart_data = self.filter_profit_chart_data(self.profit_chart_data)
        self.draw_profit_chart()

    def parse_trade_form(self):
        symbol = self.symbol_var.get().strip().upper()
        tx_type = "buy" if self.tx_type_var.get() == "买入" else "sell"
        try:
            amount = float(self.amount_var.get().strip())
            price = float(self.price_var.get().strip())
        except ValueError:
            messagebox.showerror("输入错误", "数量和价格必须是有效数字。")
            return None

        date = self.date_var.get().strip()
        return symbol, tx_type, amount, price, date

    def add_transaction(self):
        parsed = self.parse_trade_form()
        if parsed is None:
            return

        symbol, tx_type, amount, price, date = parsed
        if tx_type == "buy":
            saved = self.manager.buy(symbol, amount, price, date)
        else:
            saved = self.manager.sell(symbol, amount, price, date)

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

        old_symbol, old_index = self.selected_transaction
        symbol, tx_type, amount, price, date = parsed
        if symbol != old_symbol:
            messagebox.showwarning("暂不支持", "编辑时不能修改币种。如需换币种，请删除后重新新增。")
            return

        if self.manager.update_transaction(old_symbol, old_index, tx_type, amount, price, date):
            self.after_data_change("交易已修改")
        else:
            messagebox.showwarning("未保存", "修改失败，请检查输入和后续卖出记录。")

    def delete_selected_transaction(self):
        if self.selected_transaction is None:
            messagebox.showinfo("提示", "请先选择一条交易记录。")
            return

        if not messagebox.askyesno("确认删除", "确认删除选中的交易记录？"):
            return

        symbol, index = self.selected_transaction
        if self.manager.delete_transaction(symbol, index):
            self.clear_transaction_form()
            self.after_data_change("交易已删除")
        else:
            messagebox.showwarning("未删除", "删除失败，删除后账单可能会导致卖出数量超过持仓。")

    def on_transaction_select(self, _event):
        selection = self.transactions_tree.selection()
        if not selection:
            return

        values = self.transactions_tree.item(selection[0], "values")
        symbol, index, tx_type, date, amount, price, _total = values
        self.selected_transaction = (symbol, int(index))
        self.symbol_var.set(symbol)
        self.tx_type_var.set(tx_type)
        self.date_var.set(date)
        self.amount_var.set(amount)
        self.price_var.set(price)

    def clear_transaction_form(self):
        self.selected_transaction = None
        self.symbol_var.set("")
        self.tx_type_var.set("买入")
        self.amount_var.set("")
        self.price_var.set("")
        self.date_var.set(self.manager.now())
        self.transactions_tree.selection_remove(self.transactions_tree.selection())

    def after_data_change(self, message):
        self.refresh_symbols()
        self.refresh_transactions()
        self.status_var.set(message)

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

        self.fill_tree(self.snapshot_detail_tree, snapshot.get("rows", []), self.rate_tag_for_row)
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
