"""Interactive command-line entry point."""

from crypto_portfolio.portfolio_manager import COIN_MAP, PortfolioManager


def input_positive_float(prompt):
    while True:
        try:
            value = float(input(prompt).strip())
            if value > 0:
                return value
            print("请输入大于 0 的数字。")
        except ValueError:
            print("请输入有效数字。")


def input_symbol(prompt):
    while True:
        symbol = input(prompt).strip().upper()
        if symbol:
            return symbol
        print("币种不能为空。")


def choose_option(title, options, allow_back=True):
    if not options:
        print("暂无可选项。")
        return None

    print(f"\n{title}")
    for index, (label, _) in enumerate(options, start=1):
        print(f"{index}. {label}")
    if allow_back:
        print("0. 返回")

    while True:
        choice = input("请选择: ").strip()
        if allow_back and choice == "0":
            return None
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(options):
                return options[index - 1][1]
        print("无效选项，请重新输入。")


def choose_buy_symbol(manager):
    symbols = []
    for symbol in manager.get_symbols() + sorted(COIN_MAP.keys()):
        if symbol not in symbols:
            symbols.append(symbol)

    options = [(symbol, symbol) for symbol in symbols]
    options.append(("手动输入其他币种", "__CUSTOM__"))

    symbol = choose_option("选择买入币种", options)
    if symbol == "__CUSTOM__":
        return input_symbol("输入币种: ")
    return symbol


def choose_existing_symbol(manager, title):
    symbols = manager.get_symbols()
    if not symbols:
        print("暂无持仓。")
        return None
    return choose_option(title, [(symbol, symbol) for symbol in symbols])


def choose_history_symbol(manager):
    symbols = manager.get_symbols()
    options = [("全部币种", "")]
    options.extend((symbol, symbol) for symbol in symbols)
    return choose_option("选择查看的币种", options)


def input_buy_date(manager):
    while True:
        trade_date = input("输入购买日期(YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS，直接回车使用当前时间): ").strip()
        normalized = manager.normalize_trade_date(trade_date)
        if normalized is not None:
            return normalized


def choose_buy_order(manager, symbol):
    buy_orders = manager.get_buy_transactions(symbol)
    if not buy_orders:
        print("该币种暂无买入订单。")
        return None

    options = []
    for transaction_index, tx in buy_orders:
        label = (
            f"{tx['date']} | 数量 {tx['amount']} | "
            f"价格 {tx['price']} | 总额 {tx['total']}"
        )
        options.append((label, transaction_index))

    return choose_option("选择要删除的买入订单", options)


def choose_holdings_snapshot(manager):
    snapshots = manager.list_holdings_snapshots()
    if not snapshots:
        print("暂无历史持仓查询结果。")
        return None

    options = []
    for path, snapshot in snapshots:
        saved_at = snapshot.get("saved_at", "未知时间")
        total_value = snapshot.get("total_value", 0.0)
        total_profit = snapshot.get("total_profit", 0.0)
        label = f"{saved_at} | 总价值 ${total_value:.2f} | 总收益 {total_profit:.2f}"
        options.append((label, path))

    return choose_option("选择历史持仓查询结果", options)


def main():
    manager = PortfolioManager()

    while True:
        print("\n==============================")
        print("加密货币持仓管理系统")
        print("==============================")
        print("1. 买入")
        print("2. 卖出")
        print("3. 显示持仓")
        print("4. 交易历史")
        print("5. 资产分布")
        print("6. 删除买入订单")
        print("7. 查看历史持仓结果")
        print("8. 退出")

        choice = input("请选择操作: ").strip()

        if choice == "1":
            symbol = choose_buy_symbol(manager)
            if symbol is None:
                continue
            amount = input_positive_float("输入买入数量: ")
            price = input_positive_float("输入买入价格(USD): ")
            trade_date = input_buy_date(manager)
            manager.buy(symbol, amount, price, trade_date)

        elif choice == "2":
            symbol = choose_existing_symbol(manager, "选择卖出币种")
            if symbol is None:
                continue
            amount = input_positive_float("输入卖出数量: ")
            price = input_positive_float("输入卖出价格(USD): ")
            manager.sell(symbol, amount, price)

        elif choice == "3":
            manager.show_holdings()

        elif choice == "4":
            symbol = choose_history_symbol(manager)
            if symbol is None:
                continue
            manager.show_history(symbol)

        elif choice == "5":
            manager.show_distribution()

        elif choice == "6":
            symbol = choose_existing_symbol(manager, "选择要处理的币种")
            if symbol is None:
                continue
            transaction_index = choose_buy_order(manager, symbol)
            if transaction_index is None:
                continue
            confirm = input("确认删除该买入订单？输入 y 确认: ").strip().lower()
            if confirm == "y":
                manager.delete_buy_order(symbol, transaction_index)
            else:
                print("已取消删除。")

        elif choice == "7":
            snapshot_path = choose_holdings_snapshot(manager)
            if snapshot_path is None:
                continue
            manager.show_saved_holdings_snapshot(snapshot_path)

        elif choice == "8":
            print("再见！")
            break

        else:
            print("无效选项，请重新输入。")
