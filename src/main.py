"""
main.py

백테스트 실행
"""

from pathlib import Path
import matplotlib.pyplot as plt

from config import RESULT_DIR
from runner import Runner
from strategy import Strategy1, Strategy2


# ==================================================
# 결과 저장
# ==================================================

def save_results(results):
    for result in results:
        name = result["strategy"].__class__.__name__
        history = result["history"]
        trades = result["trades"]
        history.to_csv(RESULT_DIR / f"{name}_history.csv")
        trades.to_csv(RESULT_DIR /f"{name}_trades.csv", index=False)


# ==================================================
# 그래프
# ==================================================

def draw_chart(results):
    plt.figure(figsize=(12, 6))

    for result in results:
        history = result["history"]
        name = result["strategy"].__class__.__name__
        plt.plot(history.index, history["Portfolio"], label=name)

    plt.title("Strategy Comparison")
    plt.xlabel("Date")
    plt.ylabel("Portfolio")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(RESULT_DIR /"strategy_comparison.png", dpi=300)
    plt.close()


# ==================================================
# Main
# ==================================================

def main():
    runner = Runner()
    runner.add_strategy(Strategy1())
    runner.add_strategy(Strategy2())
    results = runner.run()

    print()
    print(runner.summary())
    print()
    save_results(results)
    draw_chart(results)


if __name__ == "__main__":
    main()    