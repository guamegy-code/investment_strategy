import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";

import {addIndicators, parseYaml, strategySnapshot} from "../src/strategy-runtime.js";

const definition = parseYaml(readFileSync(
  new URL("../../strategies/32_qqq_valuation_financial_credit_guard_no_topup.yaml", import.meta.url),
  "utf8",
));

function syntheticData(weakDay) {
  const dates = [];
  for (let day = new Date("2020-01-01T00:00:00Z"); dates.length < 280;
    day.setUTCDate(day.getUTCDate() + 1)) {
    if (day.getUTCDay() !== 0 && day.getUTCDay() !== 6) dates.push(day.toISOString().slice(0, 10));
  }
  const data = {};
  for (const ticker of ["QQQ", "BIL", "SPY", "XLF", "BAA10Y"]) {
    data[ticker] = dates.map((date, index) => {
      const price = ticker === "XLF" && index >= weakDay ? 90
        : ticker === "BAA10Y" && index >= 268 ? 1.9 : ticker === "BAA10Y" ? 1.5 : 100;
      return {Date: date, Open: price, High: price, Low: price, Close: price, Volume: 1};
    });
    addIndicators(data[ticker]);
  }
  for (const row of data.QQQ) row.VALUATION_SCORE = 50;
  return data;
}

test("32 enters financial guard when XLF weakness precedes a new BAA warning", () => {
  const data = syntheticData(260);
  const before = strategySnapshot([definition], definition,
    Object.fromEntries(Object.entries(data).map(([ticker, rows]) => [ticker, rows.slice(0, 268)])));
  const after = strategySnapshot([definition], definition,
    Object.fromEntries(Object.entries(data).map(([ticker, rows]) => [ticker, rows.slice(0, 269)])));
  assert.equal(before.notification_context.state_values.financial_guard, "FALSE");
  assert.equal(after.notification_context.state_values.financial_guard, "TRUE");
  assert.equal(after.notification_context.target_weights.QQQ, 0.35);
});

test("32 ignores XLF weakness that first appears on the BAA warning day", () => {
  const data = syntheticData(268);
  const snapshot = strategySnapshot([definition], definition,
    Object.fromEntries(Object.entries(data).map(([ticker, rows]) => [ticker, rows.slice(0, 269)])));
  assert.equal(snapshot.notification_context.state_values.financial_guard, "FALSE");
});
