"""
main.py

투자 전략 백테스트 실행 파일 (v2.2)
"""

from pathlib import Path

import matplotlib.pyplot as plt

from config import (
    RESULT_DIR,
)

from backtest import Backtest

from performance import Performance



def save_result(
    history,
    trades
):
    """
    결과 저장
    """

    history_file = (
        RESULT_DIR
        /
        "portfolio_history.csv"
    )

    trades_file = (
        RESULT_DIR
        /
        "rebalance_history.csv"
    )


    history.to_csv(
        history_file
    )


    trades.to_csv(
        trades_file,
        index=False
    )


    print(
        f"저장 완료 : {history_file}"
    )

    print(
        f"저장 완료 : {trades_file}"
    )



def draw_chart(
    history
):
    """
    포트폴리오 성장 그래프
    """

    plt.figure(
        figsize=(12,6)
    )


    plt.plot(
        history.index,
        history["Portfolio"]
    )


    plt.title(
        "Portfolio Value"
    )


    plt.xlabel(
        "Date"
    )


    plt.ylabel(
        "Value"
    )


    plt.grid(
        True
    )


    file = (
        RESULT_DIR
        /
        "portfolio_chart.png"
    )


    plt.savefig(
        file,
        dpi=300,
        bbox_inches="tight"
    )


    plt.close()


    print(
        f"그래프 저장 완료 : {file}"
    )



def main():

    print("=" * 60)
    print("Investment Strategy Backtest v2.2")
    print("=" * 60)



    # -----------------------------
    # Backtest
    # -----------------------------

    backtest = Backtest()


    history, trades = (
        backtest.run_all()
    )


    # -----------------------------
    # Performance
    # -----------------------------

    performance = Performance(
        history
    )


    summary = (
        performance.summary()
    )


    print("\n===== Performance =====")


    for key, value in summary.items():

        if isinstance(value, float):

            print(
                f"{key:<15}: {value:.4f}"
            )

        else:

            print(
                f"{key:<15}: {value}"
            )



    # -----------------------------
    # Save
    # -----------------------------

    save_result(
        history,
        trades
    )


    # -----------------------------
    # Chart
    # -----------------------------

    draw_chart(
        history
    )


    print("\n완료")



if __name__ == "__main__":

    main()