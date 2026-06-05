from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

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

        self.create_widgets()
        self.refresh_all()

    def create_widgets(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        self.holdings_tab = ttk.Frame(self.notebook)
        self.transactions_tab = ttk.Frame(self.notebook)
        self.snapshots_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.holdings_tab, text="持仓")
        self.notebook.add(self.transactions_tab, text="交易记录")
        self.notebook.add(self.snapshots_tab, text="历史持仓结果")

        self.create_holdings_tab()
        self.create_transactions_tab()
        self.create_snapshots_tab()

        status = ttk.Label(self, textvariable=self.status_var, anchor="w")
        status.pack(fill="x", padx=10, pady=8)

    def create_holdings_tab(self):
        toolbar = ttk.Frame(self.holdings_tab)
        toolbar.pack(fill="x", pady=(0, 8))

        ttk.Button(toolbar, text="查询并保存", command=self.refresh_holdings).pack(side="left")
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
        for column, title, width in (
            ("saved_at", "查询时间", 170),
            ("value", "总价值", 100),
            ("profit", "总收益", 100),
            ("path", "文件", 220),
        ):
            self.snapshots_tree.heading(column, text=title)
            self.snapshots_tree.column(column, width=width, anchor="w")
        self.snapshots_tree.pack(fill="both", expand=True)
        self.snapshots_tree.bind("<<TreeviewSelect>>", self.on_snapshot_select)

        self.snapshot_detail_tree = ttk.Treeview(
            right,
            columns=("symbol", "quantity", "avg_cost", "price", "value", "profit", "rate"),
            show="headings",
        )
        for column, title, width in (
            ("symbol", "币种", 80),
            ("quantity", "数量", 130),
            ("avg_cost", "成本价", 120),
            ("price", "当前价", 120),
            ("value", "持仓价值", 120),
            ("profit", "总收益", 120),
            ("rate", "收益率", 100),
        ):
            anchor = "w" if column == "symbol" else "e"
            self.snapshot_detail_tree.heading(column, text=title)
            self.snapshot_detail_tree.column(column, width=width, anchor=anchor)
        self.configure_rate_tags(self.snapshot_detail_tree)
        self.snapshot_detail_tree.pack(fill="both", expand=True)
        ttk.Label(right, textvariable=self.snapshot_summary_var, anchor="w").pack(
            fill="x", pady=(8, 0)
        )

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

        self.status_var.set("正在查询价格...")
        self.update_idletasks()
        prices = self.manager.get_prices()
        snapshot = self.manager.build_holdings_snapshot(prices)
        snapshot_path = self.manager.save_holdings_snapshot(snapshot)

        self.fill_tree(self.holdings_tree, snapshot["rows"], self.rate_tag_for_row)
        self.holding_summary_var.set(self.format_snapshot_summary(snapshot))
        self.refresh_snapshots()
        self.status_var.set(f"持仓查询已保存: {snapshot_path}")

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

    def fill_tree(self, tree, rows, tag_factory=None):
        for item in tree.get_children():
            tree.delete(item)
        for row in rows:
            tags = tag_factory(row) if tag_factory else ()
            tree.insert("", "end", values=row, tags=tags)

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
