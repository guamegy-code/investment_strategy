import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import {fileURLToPath} from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.resolve(here, "../../docs/google-apps-script/Code.gs"), "utf8");

function kstKey(date) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(date);
}

function runtime({holidays = [], customClosures = "", sharedCache = new Map()} = {}) {
  const holidaySet = new Set(holidays);
  let calendarReads = 0;
  const calendar = {
    getEvents(start) {
      calendarReads += 1;
      return holidaySet.has(kstKey(start)) ? [{}] : [];
    },
  };
  const context = vm.createContext({
    Date, Intl, JSON, Map, Math, Number, Object, Set, String,
    CalendarApp: {
      getCalendarById: () => calendar,
      subscribeToCalendar: () => calendar,
    },
    CacheService: {
      getScriptCache: () => ({
        get: key => sharedCache.has(key) ? sharedCache.get(key) : null,
        put: (key, value) => sharedCache.set(key, value),
      }),
    },
    PropertiesService: {
      getScriptProperties: () => ({getProperty: key => key === "KRX_CLOSED_DATES" ? customClosures : null}),
    },
    Utilities: {
      formatDate(date, _timezone, format) {
        if (format === "yyyy-MM-dd") return kstKey(date);
        const parts = new Intl.DateTimeFormat("en-US", {
          timeZone: "Asia/Seoul", hour: "numeric", minute: "numeric", hourCycle: "h23",
        }).formatToParts(date);
        const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
        if (format === "H,m") return `${Number(values.hour)},${Number(values.minute)}`;
        throw new Error(`unsupported test format: ${format}`);
      },
    },
  });
  vm.runInContext(source, context);
  return {
    call(expression) { return vm.runInContext(expression, context); },
    calendarReads() { return calendarReads; },
  };
}

test("notification evaluation is limited to the hour before the KRX open", () => {
  const app = runtime();
  assert.equal(app.call("withinNotificationWindow_(new Date('2026-09-21T08:00:00+09:00'))"), true);
  assert.equal(app.call("withinNotificationWindow_(new Date('2026-09-21T08:59:59+09:00'))"), true);
  assert.equal(app.call("withinNotificationWindow_(new Date('2026-09-21T07:59:59+09:00'))"), false);
  assert.equal(app.call("withinNotificationWindow_(new Date('2026-09-21T09:00:00+09:00'))"), false);
});

test("KRX calendar skips weekends, public holidays, Labor Day, year-end, and custom closures", () => {
  const app = runtime({holidays: ["2026-10-05"], customClosures: "2026-09-22"});
  assert.equal(app.call("isKrxTradingDay_('2026-09-21')"), true);
  assert.equal(app.call("isKrxTradingDay_('2026-09-19')"), false);
  assert.equal(app.call("isKrxTradingDay_('2026-10-05')"), false);
  assert.equal(app.call("isKrxTradingDay_('2026-05-01')"), false);
  assert.equal(app.call("isKrxTradingDay_('2026-12-31')"), false);
  assert.equal(app.call("isKrxTradingDay_('2026-09-22')"), false);
});

test("weekly and monthly summaries move to the first KRX trading day", () => {
  const app = runtime({holidays: ["2026-09-21", "2026-10-01", "2026-10-02", "2026-10-05"]});
  assert.equal(app.call("isWeeklySummaryDay_(new Date('2026-09-22T08:30:00+09:00'))"), true);
  assert.equal(app.call("isWeeklySummaryDay_(new Date('2026-09-23T08:30:00+09:00'))"), false);
  assert.equal(app.call("isMonthlySummaryDay_(new Date('2026-10-06T08:30:00+09:00'))"), true);
  assert.equal(app.call("isMonthlySummaryDay_(new Date('2026-10-07T08:30:00+09:00'))"), false);
});

test("mapped product alerts distinguish the US signal date and Korean execution date", () => {
  const app = runtime();
  const message = app.call(`messageFor_({
    type: 'REBALANCE', strategy_name: 'Mapped QQQ', market_data_at: '2026-09-18',
    mapped_products: true, execution_market_date: '2026-09-21', execution_days: 1,
    current_weights: {'379810.KS': 1}, target_weights: {'379810.KS': 0.5, '488770.KS': 0.5}
  })`);
  assert.match(message, /미국 신호 기준일: 2026-09-18/);
  assert.match(message, /한국 실행 예정일.*2026-09-21/);
  assert.match(message, /한국장 시가부터 1일/);
});

test("holiday lookup is cached across many strategy evaluations", () => {
  const sharedCache = new Map();
  const app = runtime({sharedCache});
  const started = performance.now();
  for (let index = 0; index < 1000; index++) {
    assert.equal(app.call("isKrxTradingDay_('2026-09-21')"), true);
  }
  const elapsedMs = performance.now() - started;
  assert.equal(app.calendarReads(), 1);
  assert.ok(elapsedMs < 1000, `cached calendar checks took ${elapsedMs.toFixed(1)}ms`);
  const nextTrigger = runtime({sharedCache});
  assert.equal(nextTrigger.call("isKrxTradingDay_('2026-09-21')"), true);
  assert.equal(nextTrigger.calendarReads(), 0);
});
