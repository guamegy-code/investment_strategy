import assert from "node:assert/strict";
import test from "node:test";

import {addCompositeValuationScore} from "../src/composite-valuation.js";


function marketData(length = 1800) {
  const data = {QQQ: [], SPY: [], BIL: []};
  const date = new Date("2004-01-02T00:00:00Z");
  let index = 0;
  while (data.QQQ.length < length) {
    date.setUTCDate(date.getUTCDate() + 1);
    if (date.getUTCDay() === 0 || date.getUTCDay() === 6) continue;
    const day = date.toISOString().slice(0, 10);
    const qqqRaw = 40 * Math.exp(index * 0.00045) * (1 + 0.08 * Math.sin(index / 90));
    const spyClose = 70 * Math.exp(index * 0.00028) * (1 + 0.04 * Math.sin(index / 110));
    const bilClose = 80 * Math.exp(index * (0.01 + 0.035 * (1 + Math.sin(index / 300)) / 2) / 252);
    const dividend = index % 63 === 0 ? 0.12 + index / 100000 : 0;
    data.QQQ.push({Date: day, Close: qqqRaw, RawClose: qqqRaw, Dividends: dividend});
    data.SPY.push({Date: day, Close: spyClose});
    data.BIL.push({Date: day, Close: bilClose});
    index += 1;
  }
  return data;
}

test("합성 밸류에이션은 월말 점수를 다음 달 거래일에 동일하게 적용한다", () => {
  const data = addCompositeValuationScore(marketData());
  const finite = data.QQQ.filter((row) => Number.isFinite(row.VALUATION_SCORE));

  assert.ok(finite.length > 500);
  assert.ok(finite.every((row) => row.VALUATION_SCORE >= 0 && row.VALUATION_SCORE <= 100));
  const month = finite.at(-1).Date.slice(0, 7);
  const values = new Set(finite.filter((row) => row.Date.startsWith(month)).map((row) => row.VALUATION_SCORE));
  assert.equal(values.size, 1);
});

test("나중에 추가된 가격은 과거 합성점수를 변경하지 않는다", () => {
  const short = addCompositeValuationScore(marketData(1600));
  const long = addCompositeValuationScore(marketData(1800));
  const longByDate = new Map(long.QQQ.map((row) => [row.Date, row.VALUATION_SCORE]));

  for (const row of short.QQQ.filter((item) => Number.isFinite(item.VALUATION_SCORE))) {
    assert.equal(row.VALUATION_SCORE, longByDate.get(row.Date));
  }
});
