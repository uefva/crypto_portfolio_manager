from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
import threading
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
        self.server_url_var = tk.StringVar(value="http://127.0.0.1:8765")
        self.profit_chart_data = None
        self.highlighted_symbol = None

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

        ttk.Label(toolbar, text="服务端").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.server_url_var, width=28).pack(side="left", padx=8)
        ttk.Button(toolbar, text="刷新图表", command=self.refresh_profit_chart).pack(side="left")
        ttk.Button(toolbar, text="清除高亮", command=self.clear_chart_highlight).pack(
            side="left", padx=8
        )

        self.profit_chart_canvas = tk.Canvas(
            self.profit_chart_tab,
            background="white",
            highlightthickness=1,
            highlightbackground="#d0d0d0",
        )
        self.profit_chart_canvas.pack(fill="both", expand=True)
        self.profit_chart_canvas.bind("<Configure>", lambda _event: self.draw_profit_chart())

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
        self.profit_chart_data = data
        if self.highlighted_symbol not in self.profit_chart_data["series"]:
            self.highlighted_symbol = None
        self.draw_profit_chart()

    def build_profit_chart_data(self):
        snapshots = list(reversed(self.manager.list_holdings_snapshots()))
        metric = self.chart_metric_var.get()
        value_index = 6 if metric == "收益率" else 5
        labels = []
        series = {}

        for _path, snapshot in snapshots:
            saved_at = snapshot.get("saved_at", "未知")
            labels.append(saved_at)
            for row in snapshot.get("rows", []):
                if len(row) <= value_index:
                    continue
                symbol = str(row[0])
                try:
                    value = self.parse_metric_value(row[value_index])
                except ValueError:
                    continue
                series.setdefault(symbol, []).append((len(labels) - 1, value))

        return {
            "labels": labels,
            "series": {
                symbol: points
                for symbol, points in sorted(series.items())
                if points
            },
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

        server_url = self.server_url_var.get().strip().rstrip("/")
        if not server_url:
            raise ValueError("服务端地址不能为空。")

        query = urlencode({
            "symbols": ",".join(sorted(holdings.keys())),
            "limit": "5000",
        })
        response = requests.get(
            f"{server_url}/api/prices/history?{query}",
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()

        labels = []
        series = {}
        for point in payload.get("points", []):
            prices = point.get("prices", {})
            labels.append(point.get("timestamp", "未知"))
            point_index = len(labels) - 1

            for symbol, asset in holdings.items():
                price = prices.get(symbol)
                if price is None:
                    continue

                profit = asset["quantity"] * float(price) - asset["total_cost"]
                if metric == "收益率":
                    value = profit / asset["total_cost"] * 100
                else:
                    value = profit
                series.setdefault(symbol, []).append((point_index, value))

        return {
            "labels": labels,
            "series": {
                symbol: points
                for symbol, points in sorted(series.items())
                if points
            },
            "metric": metric,
            "source": "server",
        }

    def draw_profit_chart(self):
        if not hasattr(self, "profit_chart_canvas"):
            return

        canvas = self.profit_chart_canvas
        canvas.delete("all")

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

        left = 74
        right = 190
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
            label = f"{value:.1f}%" if metric == "收益率" else f"{value:.2f}"
            canvas.create_text(chart_left - 10, y, text=label, anchor="e", fill="#555555")

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

        colors = [
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

        for color_index, (symbol, points) in enumerate(series.items()):
            selected = self.highlighted_symbol == symbol
            dimmed = self.highlighted_symbol is not None and not selected
            color = "#cfcfcf" if dimmed else colors[color_index % len(colors)]
            line_width = 4 if selected else 2
            marker_radius = 5 if selected else 3
            coords = []
            for index, value in points:
                x = x_for(index)
                y = y_for(value)
                coords.extend((x, y))
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

            legend_y = chart_top + color_index * 22
            legend_x = chart_right + 24
            tag = f"legend_{symbol}"
            canvas.create_line(
                legend_x,
                legend_y,
                legend_x + 22,
                legend_y,
                fill=color,
                width=4 if selected else 3,
                tags=(tag, "legend_item"),
            )
            text_item = canvas.create_text(
                legend_x + 30,
                legend_y,
                text=f"{symbol} 选中" if selected else symbol,
                anchor="w",
                fill="#111111" if selected else "#333333",
                font=("Microsoft YaHei UI", 9, "bold") if selected else ("Microsoft YaHei UI", 9),
                tags=(tag, "legend_item"),
            )
            bbox = canvas.bbox(text_item)
            if bbox:
                canvas.create_rectangle(
                    legend_x - 6,
                    bbox[1] - 4,
                    legend_x + 118,
                    bbox[3] + 4,
                    outline="#999999" if selected else "",
                    fill="",
                    tags=(tag, "legend_item"),
                )
            canvas.tag_bind(tag, "<Button-1>", lambda _event, sym=symbol: self.toggle_chart_highlight(sym))
            canvas.tag_bind(tag, "<Enter>", lambda _event: canvas.configure(cursor="hand2"))
            canvas.tag_bind(tag, "<Leave>", lambda _event: canvas.configure(cursor=""))

    def toggle_chart_highlight(self, symbol):
        if self.highlighted_symbol == symbol:
            self.highlighted_symbol = None
        else:
            self.highlighted_symbol = symbol
        self.draw_profit_chart()

    def clear_chart_highlight(self):
        self.highlighted_symbol = None
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
