"""Profit-chart tab behavior for the desktop app.

The chart logic depends on many Tkinter widgets owned by ``PortfolioApp``. A
mixin keeps that coupling explicit while moving the large drawing and history
fetching code out of the main window module.
"""

from datetime import datetime, timedelta
from urllib.parse import urlparse
import time
import tkinter as tk
from tkinter import ttk

import requests

from crypto_portfolio.market_data import (
    CATEGORY_ALL,
    CATEGORY_CRYPTO,
    CATEGORY_FUND,
    CATEGORY_STOCK,
)


CATEGORY_OPTIONS = (CATEGORY_FUND, CATEGORY_STOCK, CATEGORY_CRYPTO)


class ProfitChartMixin:
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
