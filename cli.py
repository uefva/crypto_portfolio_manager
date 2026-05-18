from portfolio_manager import PortfolioManager


def input_float(prompt):
    while True:
        try:
            return float(input(prompt).strip())
        except ValueError:
            print("请输入有效数字。")


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
        print("6. 退出")

        choice = input("请选择操作: ").strip()

        if choice == "1":
            symbol = input("输入币种: ").strip().upper()
            amount = input_float("输入买入数量: ")
            price = input_float("输入买入价格(USD): ")
            manager.buy(symbol, amount, price)

        elif choice == "2":
            symbol = input("输入币种: ").strip().upper()
            amount = input_float("输入卖出数量: ")
            price = input_float("输入卖出价格(USD): ")
            manager.sell(symbol, amount, price)

        elif choice == "3":
            manager.show_holdings()

        elif choice == "4":
            symbol = input("输入币种(直接回车查看所有): ").strip()
            manager.show_history(symbol)

        elif choice == "5":
            manager.show_distribution()

        elif choice == "6":
            print("再见！")
            break

        else:
            print("无效选项，请重新输入。")
