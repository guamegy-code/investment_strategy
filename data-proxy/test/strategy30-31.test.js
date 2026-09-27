import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";

import {addIndicators, parseYaml, runStrategy, runStrategyIncremental, selectNotificationAlerts, strategySnapshot} from "../src/strategy-runtime.js";

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

function stressAndRecoveryData() {
  const dates = [];
  for (let day = new Date("2020-01-01T00:00:00Z"); dates.length < 480;
    day.setUTCDate(day.getUTCDate() + 1)) {
    if (day.getUTCDay() !== 0 && day.getUTCDay() !== 6) dates.push(day.toISOString().slice(0, 10));
  }
  const data = {};
  for (const ticker of ["QQQ", "SPY", "BIL", "TDF2050_PROXY", "BAA10Y"]) {
    data[ticker] = dates.map((date, index) => {
      const stress = Math.max(0, Math.min((index - 245) / 25, 1));
      const recovery = Math.max(0, Math.min((index - 305) / 80, 1));
      const pressure = stress * (1 - recovery);
      const price = ticker === "QQQ" ? 100 * (1 - .35 * pressure)
        : ticker === "SPY" ? 100 * (1 - .08 * pressure)
        : ticker === "BAA10Y" ? 1.5 + 1.2 * pressure : 100;
      return {Date: date, Open: price, High: price, Low: price, Close: price, Volume: 1};
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

test("30 and 31 preserve Korean labels and notify when warnings clear", () => {
  for (const definition of definitions) {
    const policy = definition.notifications;
    assert.equal(policy.states.credit_guard.values.TRUE, "1차 방어");
    assert.equal(policy.states.credit_guard.values.FALSE, "정상");
    assert.ok(policy.states.structural_bear_mode.alerts.some(rule => rule.to === "FALSE"));
    assert.ok(policy.states.stage.alerts.some(rule => rule.to === 0));
    assert.ok(policy.states.trend_mode.alerts.some(rule => rule.to === "BULL"));
    assert.ok(policy.prealerts.every(rule => rule.reset_message));

    const context = (changes = [], prealerts = []) => ({
      notification_policy: policy, state_values: {credit_guard: "FALSE"},
      state_changes: changes, confirmation_started: [], prealerts,
      current_weights: {QQQ: 1, BIL: 0}, target_weights: {QQQ: 1, BIL: 0},
      target_deviation: 0,
    });
    const history = [
      {date: "2026-09-01", notificationContext: context([], [{id: "test", message: "비중 주의", matched: true, reset: false}])},
      {date: "2026-09-02", notificationContext: context([
        {name: "structural_bear_mode", previous: "TRUE", current: "FALSE"},
      ], [{id: "test", message: "비중 주의", reset_message: "비중 주의 해제", matched: false, reset: true}])},
    ];
    const result = selectNotificationAlerts(history);
    assert.deepEqual(result.alerts.map(alert => alert.type), ["PREALERT", "RESOLVED"]);
    assert.match(result.alerts[1].reason_text, /구조적 약세: 감지 \(TRUE\) → 정상 \(FALSE\)/);
    assert.match(result.alerts[1].reason_text, /비중 주의 해제/);
  }
});

test("30 and 31 do not call a zero-trade guard change a rebalance", () => {
  for (const definition of definitions) {
    const context = {
      notification_policy: definition.notifications,
      state_values: {credit_guard: "TRUE"},
      state_changes: [{name: "credit_guard", previous: "FALSE", current: "TRUE"}],
      confirmation_started: [], prealerts: [],
      current_weights: {QQQ: 0, BIL: 1}, target_weights: {QQQ: 0, BIL: 1},
      target_deviation: 0,
    };
    const result = selectNotificationAlerts([{date: "2026-09-01", target: {QQQ: 0, BIL: 1}, notificationContext: context}]);
    assert.equal(result.alerts.length, 1);
    assert.equal(result.alerts[0].type, "PREALERT");
    assert.match(result.alerts[0].reason_text, /상대약세·신용 경보: 정상 \(FALSE\) → 1차 방어 \(TRUE\)/);
  }
});

test("30 and 31 distinguish an improving stage from an active warning", () => {
  for (const definition of definitions) {
    const context = change => ({
      notification_policy: definition.notifications, state_changes: [change],
      state_values: {[change.name]: change.current}, confirmation_started: [], prealerts: [],
      current_weights: {QQQ: 1}, target_weights: {QQQ: 1}, target_deviation: 0,
    });
    const history = [
      {date: "2026-09-01", notificationContext: context({name: "stage", previous: 3, current: 2})},
      {date: "2026-09-02", notificationContext: context({name: "trend_mode", previous: "RECOVERY", current: "BULL"})},
    ];
    const selected = selectNotificationAlerts(history);
    assert.deepEqual(selected.alerts.map(alert => alert.type), ["UPDATE", "RESOLVED"]);
  }
});

test("an immediate rebalance explains missing prior warning and merges same-date events", () => {
  const policy = definitions[0].notifications;
  const context = {
    notification_policy: policy, state_values: {structural_bear_mode: "TRUE"},
    state_changes: [{name: "structural_bear_mode", previous: "FALSE", current: "TRUE"}],
    confirmation_started: [],
    prealerts: [{id: "drift", message: "괴리 주의", matched: true, reset: false}],
    current_weights: {QQQ: 1, BIL: 0}, target_weights: {QQQ: .5, BIL: .5},
    target_deviation: .5,
  };
  const result = selectNotificationAlerts([{date: "2026-09-01", target: {QQQ: .5, BIL: .5}, notificationContext: context}]);
  assert.equal(result.alerts.length, 1);
  assert.equal(result.alerts[0].type, "REBALANCE");
  assert.match(result.alerts[0].reason_text, /괴리 주의/);
  assert.match(result.alerts[0].reason_text, /사전주의 없이 즉시 실행 조건 충족/);
});

test("30 and 31 report structural recovery and avoid zero-trade rebalance alerts on a full path", () => {
  const data = stressAndRecoveryData();
  const seed = Object.fromEntries(Object.entries(data).map(([ticker, rows]) => [ticker, rows.slice(0, 245)]));
  for (const definition of definitions) {
    const snapshot = strategySnapshot(definitions, definition, seed);
    const result = runStrategyIncremental(definitions, definition, data, snapshot);
    const selected = selectNotificationAlerts(result.history, {latest_context: snapshot.notification_context});
    assert.ok(selected.alerts.some(alert => alert.reason_text.includes("구조적 약세 해제")));
    assert.ok(selected.alerts.some(alert => alert.type === "REBALANCE" && alert.reason_text.includes("사전주의 없이")));
    assert.ok(selected.alerts.filter(alert => alert.type === "REBALANCE").every(alert => alert.target_deviation > 1e-8));
  }
});
