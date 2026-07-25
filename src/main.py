"""Run investment-strategy backtests and render the chart."""

from chart import draw_chart
from config import RESULT_DIR
from runner import Runner
from strategy import Strategy1, Strategy2, TrendStrategy, BASIC_BANG_DIV


def save_results(results):
    for result in results:
        name = result["strategy"].__class__.__name__
        result["history"].to_csv(RESULT_DIR / f"{name}_history.csv")
        result["trades"].to_csv(RESULT_DIR / f"{name}_trades.csv", index=False)


def main():
    runner = Runner()
    for strategy in (Strategy1(), Strategy2(), TrendStrategy(), BASIC_BANG_DIV()):
        runner.add_strategy(strategy)
    results = runner.run()
    print("\n", runner.summary(), "\n")
    save_results(results)
    draw_chart(results)


if __name__ == "__main__":
    main()
