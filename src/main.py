"""Run investment-strategy backtests and render the chart."""

from chart import draw_chart

from config import RESULT_DIR, START_DATE, END_DATE
from runner import Runner
from strategy import Strategy1, Strategy2, TrendStrategy, BASIC_BANG_DIV, RETIREMENT_7030_BAND, ASYMMETRIC_TREND_BAND

# ==================================================
# 결과 저장
# ==================================================

def save_results(results):
    for result in results:
        name = result["strategy"].__class__.__name__
        result["history"].to_csv(RESULT_DIR / f"{name}_history.csv")
        result["trades"].to_csv(RESULT_DIR / f"{name}_trades.csv", index=False)


def main():
    runner = Runner()
    for strategy in (Strategy1(), Strategy2(), TrendStrategy(), BASIC_BANG_DIV(),RETIREMENT_7030_BAND(),ASYMMETRIC_TREND_BAND()):
        runner.add_strategy(strategy)
    results = runner.run()

# 💡 2. 백테스트가 시작되기 전, 터미널에 기간을 예쁘게 출력합니다.
    print("=" * 50)
    print(f" 🚀 백테스트 기간: {START_DATE} ~ {END_DATE}")
    print("=" * 50)

    print("\n", runner.summary(), "\n")
    save_results(results)
    draw_chart(results)


if __name__ == "__main__":
    main()
