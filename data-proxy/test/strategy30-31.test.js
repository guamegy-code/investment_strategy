import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";

import {addIndicators, parseYaml, runStrategy, strategySnapshot} from "../src/strategy-runtime.js";

const definitions = [30, 31].map(number => {
  const filename = number === 30
    ? "30_qqq_valuation_credit_guard_no_topup.yaml"
    : "31_band_7030_tdf_valuation_credit_guard_no_topup.yaml";
  return parseYaml(readFileSync(new URL(`../../strategies/${filename}`, import.meta.url), "utf8"));
});

function syntheticData() {
  const dates = [];
  for (let day = new Date("2020-01-01T00:00:00Z"); dates.length < 330;
    day.setUTCDate(day.getUTCDate() + 1)) {
    if (day.getUTCDay() !== 0 && day.getUTCDay() !== 6) {
      dates.push(day.toISOString().slice(0, 10));
    }
  }
  const data = {};
  for (const ticker of ["QQQ", "SPY", "BIL", "TDF2050_PROXY", "BAA10Y"]) {
    data[ticker] = dates.map((date, index) => {
      const decline = Math.max(0, Math.min((index - 260) / 30, 1));
      const price = ticker === "QQQ" ? 100 * (1 - 0.35 * decline)
        : ticker === "SPY" ? 100 * (1 - 0.08 * decline)
        : ticker === "BAA10Y" ? 1.5 + decline : 100;
      return {Date: date, Open: price, High: price, Low: price,
        Close: price, Volume: 1};
    });
    addIndicators(data[ticker]);
  }
  for (const row of data.QQQ) row.VALUATION_SCORE = 50;
  return data;
}

test("30 and 31 execute with the same lagged credit observations in Worker runtime", () => {
  const data = syntheticData();
  for (const definition of definitions) {
    const history = runStrategy(definitions, definition, data);
    const snapshot = strategySnapshot(definitions, definition, data);
    assert.ok(history.length > 20);
    assert.ok(history.every(row => Number.isFinite(row.value)));
    assert.equal(snapshot.notification_context.state_values.deep_guard, "TRUE");
    assert.ok(snapshot.notification_context.target_weights.QQQ <= 0.20);
  }
});
