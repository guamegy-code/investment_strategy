import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {gzipSync} from "node:zlib";

import worker, {createFallbackTickerLoader, loadPriceRange, loadTicker} from "../src/index.js";
import {
  mapProductTarget, parseYaml, runStrategy, runStrategyIncremental,
  selectNotificationAlerts, strategySnapshot, strategyTickers,
} from "../src/strategy-runtime.js";

const chartPayload = {
  chart: {
    result: [{
      timestamp: [1_704_067_200],
      indicators: {
        quote: [{
          open: [100], high: [103], low: [99], close: [102], volume: [1_000],
        }],
        adjclose: [{adjclose: [101]}],
      },
      events: {dividends: {"1704067200": {date: 1_704_067_200, amount: 0.25}}},
    }],
  },
};

test("loadTicker retries the second Yahoo chart host after a 429", async (context) => {
  const originalFetch = globalThis.fetch;
  const urls = [];
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return urls.length === 1
      ? new Response("rate limited", {status: 429})
      : Response.json(chartPayload);
  };

  const rows = await loadTicker("SPY", 1_700_000_000, 1_710_000_000);

  assert.equal(urls.length, 2);
  assert.match(urls[0], /query1\.finance\.yahoo\.com/);
  assert.match(urls[1], /query2\.finance\.yahoo\.com/);
  assert.equal(rows[0].date, "2024-01-01");
  assert.equal(rows[0].close, 101);
  assert.equal(rows[0].raw_close, 102);
  assert.equal(rows[0].dividends, 0.25);
});

test("loadTicker maps the legacy KRW symbol to Yahoo's complete USDKRW history", async (context) => {
  const originalFetch = globalThis.fetch;
  let requestedUrl = "";
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url) => {
    requestedUrl = String(url);
    return Response.json(chartPayload);
  };

  await loadTicker("KRW=X", 1_700_000_000, 1_710_000_000);

  assert.match(requestedUrl, /USDKRW%3DX/);
  assert.doesNotMatch(requestedUrl, /\/KRW%3DX/);
});

test("loadPriceRange serves cached rows when every upstream host is rate limited", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response("rate limited", {status: 429});
  const cached = [{
    date: "2024-01-02", open: 100, high: 103, low: 99, close: 102, volume: 1_000,
  }];
  const env = {
    MARKET_DATA: {
      get: async () => JSON.stringify(cached),
      put: async () => { throw new Error("cache should not be rewritten"); },
    },
  };

  const result = await loadPriceRange(env, null, "SPY", 1_704_067_200, 1_704_240_000);

  assert.equal(result.stale, true);
  assert.deepEqual(result.rows, [{
    date: "2024-01-02", open: 100, high: 103, low: 99, close: 102, volume: 1_000,
  }]);
});

test("recent KV rows never suppress a full history request without a history store", async (context) => {
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => {
    fetchCount += 1;
    return Response.json(chartPayload);
  };
  const cached = [
    {date:"2024-01-02",open:100,high:101,low:99,close:100,volume:1},
    {date:"2024-01-03",open:101,high:102,low:100,close:101,volume:1},
  ];
  const env = {MARKET_DATA:{
    get:async()=>JSON.stringify(cached),
    put:async()=>{},
  }};

  await loadPriceRange(
    env,null,"SPY",
    Math.floor(Date.parse("2024-01-02")/1000),
    Math.floor(Date.parse("2024-01-03T12:00:00Z")/1000),
  );

  assert.equal(fetchCount,1);
});

test("loadPriceRange still fails when no cached data is available", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response("rate limited", {status: 429});
  const env = {MARKET_DATA: {get: async () => null, put: async () => {}}};

  await assert.rejects(
    loadPriceRange(env, null, "SPY", 1_704_067_200, 1_704_240_000),
    /SPY: upstream returned 429/,
  );
});

test("loadPriceRange returns only the requested ticker from the static fallback", async (context) => {
  const originalFetch = globalThis.fetch;
  const fallbackRequests = [];
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url) => {
    if (String(url).includes("finance.yahoo.com")) return new Response("rate limited", {status: 429});
    fallbackRequests.push(String(url));
    const csv = String(url).includes("U1BZ")
      ? "Date,Open,High,Low,Close,Volume\n2024-01-02,100,103,99,102,1000\n"
      : "Date,Open,High,Low,Close,Volume\n2024-01-02,200,204,198,203,2000\n";
    return new Response(gzipSync(csv), {headers: {"Content-Type": "application/gzip"}});
  };
  const writes = [];
  const env = {
    MARKET_DATA_FALLBACK_BASE_URL: "https://example.test/market-data/",
    MARKET_DATA: {
      get: async () => null,
      put: async (key, value) => { writes.push([key, JSON.parse(value)]); },
    },
  };
  const loadFallbackTicker = createFallbackTickerLoader(env);

  const [spy, qqq] = await Promise.all([
    loadPriceRange(env, null, "SPY", 1_704_067_200, 1_704_240_000, loadFallbackTicker),
    loadPriceRange(env, null, "QQQ", 1_704_067_200, 1_704_240_000, loadFallbackTicker),
  ]);

  assert.deepEqual(fallbackRequests.sort(), [
    "https://example.test/market-data/UVFR.csv.gz",
    "https://example.test/market-data/U1BZ.csv.gz",
  ].sort());
  assert.equal(spy.stale, true);
  assert.deepEqual(spy.rows.map(row => row.close), [102]);
  assert.deepEqual(qqq.rows.map(row => row.close), [203]);
  assert.deepEqual(writes, []);
});

test("loadPriceRange combines R2 history with the small recent KV overlay", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response("rate limited", {status: 429});
  const cached = [{
    date: "2026-08-25", open: 210, high: 212, low: 209, close: 211, volume: 2_100,
  }];
  const fallback = [{
    date: "2024-01-02", open: 200, high: 204, low: 198, close: 203, volume: 2_000,
  }];
  const writes = [];
  const env = {MARKET_DATA: {
    get: async key => key === "recent-prices:QQQ" ? JSON.stringify(cached) : null,
    put: async (key, value) => { writes.push([key, JSON.parse(value)]); },
  }, MARKET_HISTORY: {
    get: async () => ({body: new Response(gzipSync("Date,Open,High,Low,Close,Volume\n2024-01-02,200,204,198,203,2000\n")).body}),
  }};

  const result = await loadPriceRange(
    env,
    null,
    "QQQ",
    Math.floor(Date.parse("2024-01-01") / 1000),
    Math.floor(Date.parse("2026-08-25T12:00:00Z") / 1000),
    async () => fallback,
  );

  assert.equal(result.stale, true);
  assert.deepEqual(result.rows.map(row => row.date), ["2024-01-02", "2026-08-25"]);
  assert.deepEqual(writes, []);
});

test("loadPriceRange enriches an existing row when Yahoo supplies raw close metadata", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => Response.json(chartPayload);
  const writes = [];
  const env = {
    MARKET_DATA: {get: async () => null, put: async (...args) => { writes.push(args); }},
    MARKET_HISTORY: {
      get: async () => ({body: new Response(gzipSync("Date,Open,High,Low,Close,Volume\n2024-01-01,99.01960784313727,101.99019607843138,98.02941176470588,101,1000\n")).body}),
    },
  };

  const result = await loadPriceRange(env, null, "SPY", 1_704_067_200, 1_704_240_000);

  assert.equal(result.rows.length, 1);
  assert.equal(writes.length, 1);
  const enriched = JSON.parse(writes[0][1]);
  assert.equal(enriched[0].raw_close, 102);
  assert.equal(enriched[0].dividends, 0.25);
});

test("loadPriceRange builds a KRW-adjusted price series from asset and FX rows", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response("rate limited", {status: 429});
  const fallback = async (ticker) => ticker === "QQQ" ? [{
    date: "2024-01-02", open: 100, high: 103, low: 99, close: 102, volume: 1_000,
  }] : [{
    date: "2024-01-02", open: 1300, high: 1300, low: 1300, close: 1300, volume: 0,
  }];

  const result = await loadPriceRange(
    {}, null, "QQQ_KRW", 1_704_067_200, 1_704_240_000, fallback,
  );

  assert.equal(result.stale, true);
  assert.deepEqual(result.rows, [{
    date: "2024-01-02", open: 130000, high: 133900, low: 128700, close: 132600, volume: 1_000,
  }]);
});

test("error responses are never cached by the browser", async () => {
  const result = await worker.fetch(new Request("https://example.test/prices"), {}, null);

  assert.equal(result.status, 400);
  assert.equal(result.headers.get("Cache-Control"), "no-store");
});

test("price cache ignores runtime versions and ticker order", async (context) => {
  const originalCaches = globalThis.caches;
  let matchedUrl = "";
  context.after(() => { globalThis.caches = originalCaches; });
  globalThis.caches = {default: {
    match: async (request) => {
      matchedUrl = request.url;
      return Response.json({data: {}}, {headers: {"Cache-Control": "public, max-age=43200"}});
    },
  }};

  const result = await worker.fetch(new Request(
    "https://example.test/prices?tickers=SPY,BIL&start=2024-01-01&runtime=old",
  ), {}, null);

  assert.equal(new URL(matchedUrl).searchParams.get("tickers"), "BIL,SPY");
  assert.equal(new URL(matchedUrl).searchParams.has("runtime"), false);
  assert.equal(result.headers.get("Server-Timing"), 'edge-cache;desc="HIT"');
});

test("mapped risk products keep their current mix while risk stays above 70%", () => {
  const source = {
    assets: {risk: ["QQQ"]},
    parameters: {canonical_risk_weight: 0.70},
  };
  const definition = {products: {
    QQQ: {PRODUCT_A: "50%", PRODUCT_B: "50%"},
    BND: {PRODUCT_C: "100%"},
  }};
  const actual = {PRODUCT_A: 0.50, PRODUCT_B: 0.26, PRODUCT_C: 0.24};

  assert.deepEqual(
    mapProductTarget({QQQ: 0.76, BND: 0.24}, definition, source, actual),
    {PRODUCT_A: 0.50, PRODUCT_B: 0.26, PRODUCT_C: 0.24},
  );
  assert.deepEqual(
    mapProductTarget({QQQ: 0.70, BND: 0.30}, definition, source, actual),
    {PRODUCT_A: 0.35, PRODUCT_B: 0.35, PRODUCT_C: 0.30},
  );
});

test("state confirmation resets when an intervening day does not match", () => {
  const definition = {
    strategy: {id: "confirmation-reset", name: "Confirmation reset", version: 1},
    assets: {required: ["QQQ"]},
    state: {
      defense_mode: {
        initial: "WARNING",
        rules: [{when: "QQQ.close < 90", set: "DEFENSE", confirm: 2}],
      },
    },
    target: [
      {when: "state.defense_mode == 'DEFENSE'", weights: {QQQ: "100%"}},
      {weights: {QQQ: "100%"}},
    ],
    execution: {days: 1},
  };
  const closes = [80, 100, 80, 80];
  const dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"];
  const data = {QQQ: dates.map((Date, index) => ({
    Date, Open: closes[index], High: closes[index], Low: closes[index],
    Close: closes[index], Volume: 1,
  }))};

  const history = runStrategy([definition], definition, data);

  assert.deepEqual(history.map(row => row.state), [
    "WARNING", "WARNING", "WARNING", "DEFENSE",
  ]);
});

test("strategy 24 Korean-commented YAML is available to the Worker runtime", () => {
  const source = readFileSync(
    new URL("../../strategies/24_qqq_valuation_breakdown_defense.yaml", import.meta.url),
    "utf8",
  );
  const definition = parseYaml(source);

  assert.equal(definition.strategy.id, "qqq-valuation-breakdown-defense");
  assert.equal(definition.strategy.enabled, false);
  assert.equal(definition.state.defense_mode.initial, "NORMAL");
  assert.equal(definition.state.defense_mode.rules[2].confirm, 6);
  assert.deepEqual(strategyTickers([definition], definition), ["QQQ", "BIL", "SPY"]);
});

test("strategy 25 Korean-commented YAML preserves the balanced state rules", () => {
  const source = readFileSync(
    new URL("../../strategies/25_qqq_valuation_breakdown_balanced.yaml", import.meta.url),
    "utf8",
  );
  const definition = parseYaml(source);

  assert.equal(definition.strategy.id, "qqq-valuation-breakdown-balanced");
  assert.equal(definition.strategy.enabled, true);
  assert.equal(definition.strategy.version, 2);
  assert.equal(definition.state.defense_mode.rules[3].confirm, 6);
  assert.equal(definition.state.defense_mode.rules[4].confirm, 2);
  assert.deepEqual(definition.target[1].weights, {QQQ: "30%", BIL: "70%"});
  assert.match(definition.rebalance[0].when, /weight_deviation\('QQQ'\) <= -4%/);
  assert.equal(definition.notifications.prealerts[0].id, "qqq-underweight");
  assert.deepEqual(strategyTickers([definition], definition), ["QQQ", "BIL", "SPY"]);
});

test("strategy 25 runtime enters immediately and recovers after two days", () => {
  const source = readFileSync(
    new URL("../../strategies/25_qqq_valuation_breakdown_balanced.yaml", import.meta.url),
    "utf8",
  );
  const definition = parseYaml(source);
  const dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"];
  const qqq = [
    {Close: 105, EMA20: 103, EMA55: 100, EMA200: 98, ROC5: 3, ROC20: 5, ROC60: 8, EMA20_SLOPE5: 2, EMA200_SLOPE20: 1, VALUATION_SCORE: 70},
    {Close: 80, EMA20: 95, EMA55: 96, EMA200: 70, ROC5: -5, ROC20: -8, ROC60: 2, EMA20_SLOPE5: -2, EMA200_SLOPE20: 1, VALUATION_SCORE: 45},
    {Close: 105, EMA20: 103, EMA55: 100, EMA200: 98, ROC5: 3, ROC20: 5, ROC60: 8, EMA20_SLOPE5: 2, EMA200_SLOPE20: 1, VALUATION_SCORE: 45},
    {Close: 106, EMA20: 103, EMA55: 100, EMA200: 98, ROC5: 3, ROC20: 5, ROC60: 8, EMA20_SLOPE5: 2, EMA200_SLOPE20: 1, VALUATION_SCORE: 45},
  ];
  const rows = (values) => dates.map((Date, index) => ({
    Date, Open: values[index].Close, High: values[index].Close,
    Low: values[index].Close, Volume: 1, ...values[index],
  }));
  const data = {
    QQQ: rows(qqq),
    BIL: rows(dates.map(() => ({Close: 100}))),
    SPY: rows(dates.map(() => ({Close: 105, EMA20: 103, ROC5: 1}))),
  };

  const history = runStrategy([definition], definition, data);

  assert.deepEqual(history.map(row => row.state), [
    "NORMAL", "DEFENSE", "DEFENSE", "NORMAL",
  ]);
  assert.ok(history.some(row => row.target?.QQQ === 0.3 && row.target?.BIL === 0.7));
});

test("strategy 26 Korean-commented YAML preserves retirement defense priority", () => {
  const source = readFileSync(
    new URL("../../strategies/26_band_7030_tdf_valuation_defense.yaml", import.meta.url),
    "utf8",
  );
  const definition = parseYaml(source);

  assert.equal(definition.strategy.id, "band-7030-tdf-valuation-defense");
  assert.equal(definition.strategy.enabled, true);
  assert.equal(definition.strategy.version, 2);
  assert.equal(definition.state.defense_mode.rules[3].confirm, 6);
  assert.equal(definition.state.defense_mode.rules[4].confirm, 2);
  assert.deepEqual(definition.target[0].weights, {
    QQQ: "0%", TDF2050_PROXY: "20%", BIL: "80%",
  });
  assert.deepEqual(definition.target[2].weights, {
    QQQ: "30%", TDF2050_PROXY: "0%", BIL: "70%",
  });
  assert.match(definition.rebalance[0].when, /weight_deviation\('QQQ'\) <= -4%/);
  assert.equal(definition.notifications.prealerts[0].id, "qqq-underweight");
  assert.deepEqual(
    strategyTickers([definition], definition),
    ["QQQ", "TDF2050_PROXY", "BIL", "SPY"],
  );
});

function notificationFixture() {
  const source = {
    strategy: {id: "notification-source", name: "Notification source", version: 1},
    assets: {required: ["QQQ", "BIL"]},
    variables: {
      risk_off_score: "count(QQQ.close < 90)",
      recovery_score: "count(QQQ.close >= 90)",
    },
    state: {
      defense_mode: {
        initial: "NORMAL",
        rules: [
          {when: "state.defense_mode == 'NORMAL' and QQQ.close < 90", set: "WARNING"},
          {when: "state.defense_mode == 'WARNING' and QQQ.close < 90", set: "DEFENSE", confirm: 2},
        ],
      },
      trend_mode: {initial: "BULL", rules: []},
    },
    target: [
      {when: "state.defense_mode == 'DEFENSE'", weights: {QQQ: "30%", BIL: "70%"}},
      {weights: {QQQ: "100%", BIL: "0%"}},
    ],
    notifications: {
      weekly: true,
      states: {
        defense_mode: {label: "방어 상태", alerts: [{on: "confirmation_started", to: ["DEFENSE"]}, {from: "NORMAL", to: "WARNING", message: "밸류에이션 급락을 추가 확인 중"}, {to: "DEFENSE", message: "방어 진입"}]},
      },
      variables: {risk_off_score: {label: "약세 신호", max: 1}},
      market: [{ticker: "QQQ", field: "close", label: "가격", format: "price"}],
      prealerts: [{id: "drift", when: "target_deviation() >= 5%", reset_when: "target_deviation() < 4%", message: "목표 비중 괴리 경고"}],
    },
    rebalance: [{check: "daily", when: "target_deviation() >= 7.5%"}],
    execution: {days: 1},
  };
  const dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"];
  const closes = [100, 80, 80, 80];
  const rows = values => dates.map((Date, index) => ({
    Date, Open: values[index], High: values[index], Low: values[index],
    Close: values[index], Volume: 1,
  }));
  return {source, dates, data: {QQQ: rows(closes), BIL: rows([100, 100, 100, 100])}};
}

test("incremental snapshots inherit states added by a strategy upgrade", () => {
  const {source, data} = notificationFixture();
  const seedData = Object.fromEntries(Object.entries(data).map(([ticker, rows]) => [ticker, rows.slice(0, 1)]));
  const snapshot = strategySnapshot([source], source, seedData);
  const upgraded = structuredClone(source);
  upgraded.state.structural_bear_mode = {
    initial: "FALSE",
    rules: [{
      when: "state.structural_bear_mode == 'FALSE' and QQQ.close < 90",
      set: "TRUE",
    }],
  };
  upgraded.notifications.states.structural_bear_mode = {label: "구조적 약세"};

  const result = runStrategyIncremental([upgraded], upgraded, data, snapshot);

  assert.equal(result.history[0].notificationContext.state_values.structural_bear_mode, "TRUE");
  assert.deepEqual(result.history[0].notificationContext.state_changes.find(
    change => change.name === "structural_bear_mode",
  ), {name: "structural_bear_mode", previous: "FALSE", current: "TRUE"});
});

test("notification context emits selected prealerts and a detailed rebalance", () => {
  const {source, data} = notificationFixture();
  const seedData = Object.fromEntries(Object.entries(data).map(([ticker, rows]) => [ticker, rows.slice(0, 1)]));
  const snapshot = strategySnapshot([source], source, seedData);
  const result = runStrategyIncremental([source], source, data, snapshot);
  const selected = selectNotificationAlerts(result.history, {
    deviation_armed: false, latest_context: snapshot.notification_context,
  });

  assert.deepEqual(selected.alerts.map(alert => alert.type), ["PREALERT", "PREALERT", "REBALANCE"]);
  assert.deepEqual(selected.alerts[0].state_values, {defense_mode: "WARNING", trend_mode: "BULL"});
  assert.match(selected.alerts[0].reason_text, /방어 상태: NORMAL → WARNING/);
  assert.match(selected.alerts[0].reason_text, /밸류에이션 급락/);
  assert.deepEqual(selected.alerts[1].confirmations, [{
    name: "defense_mode", desired: "DEFENSE", days: 1, required_days: 2,
  }]);
  assert.match(selected.alerts[1].reason_text, /방어 상태: DEFENSE 확인 시작 \(1\/2일\)/);
  assert.deepEqual(selected.alerts[2].target_weights, {QQQ: .3, BIL: .7});
  assert.deepEqual(selected.alerts[2].previous_target_weights, {QQQ: 1, BIL: 0});
  assert.equal(selected.alerts[2].target_changed, true);
  assert.equal(selected.alerts[2].market.qqq.close, 80);
});

test("custom notification DSL supports arbitrary state names and presentation", () => {
  const {source, data} = notificationFixture();
  data.QQQ.forEach((row, index) => { row.ROC1 = index ? -20 : 0; });
  source.notifications = {
    weekly: false,
    states: {defense_mode: {label: "내 방어"}},
    variables: {risk_off_score: {label: "내 약세", max: 1}},
    market: [{ticker: "QQQ", field: "roc1", label: "하루", format: "percent"}],
    prealerts: [{id: "drift", when: "target_deviation() >= 5%", reset_when: "target_deviation() < 4%", message: "사용자 정의 괴리 경고"}],
  };
  const seedData = Object.fromEntries(Object.entries(data).map(([ticker, rows]) => [ticker, rows.slice(0, 1)]));
  const snapshot = strategySnapshot([source], source, seedData);
  const result = runStrategyIncremental([source], source, data, snapshot);
  const selected = selectNotificationAlerts(result.history, {latest_context: snapshot.notification_context});

  assert.equal(selected.alerts[0].type, "PREALERT");
  assert.match(selected.alerts[0].reason_text, /내 방어: NORMAL → WARNING/);
  assert.deepEqual(selected.alerts[0].notification_display.states, [{name: "defense_mode", label: "내 방어", value: "WARNING"}]);
  assert.equal(selected.alerts[0].notification_display.weekly, false);
  assert.equal(selected.alerts[0].notification_display.market[0].label, "하루");
});

test("mapped product strategy inherits source context and reports product weights", () => {
  const {source, data} = notificationFixture();
  const mapped = {
    strategy: {id: "notification-products", name: "Notification products", version: 1},
    source: source.strategy.id,
    products: {QQQ: {PRODUCT_Q: "100%"}, BIL: {PRODUCT_C: "100%"}},
  };
  data.PRODUCT_Q = data.QQQ.map(row => ({...row}));
  data.PRODUCT_C = data.BIL.map(row => ({...row}));
  const definitions = [source, mapped];
  const seedData = Object.fromEntries(Object.entries(data).map(([ticker, rows]) => [ticker, rows.slice(0, 1)]));
  const snapshot = strategySnapshot(definitions, mapped, seedData);
  const result = runStrategyIncremental(definitions, mapped, data, snapshot);
  const selected = selectNotificationAlerts(result.history, {
    deviation_armed: false, latest_context: snapshot.notification_context,
  });
  const action = selected.alerts.find(alert => alert.type === "REBALANCE");

  assert.equal(action.mapped_products, true);
  assert.equal(action.source_strategy_id, source.strategy.id);
  assert.deepEqual(action.state_values, {defense_mode: "DEFENSE", trend_mode: "BULL"});
  assert.deepEqual(action.target_weights, {PRODUCT_Q: .3, PRODUCT_C: .7});
  assert.deepEqual(action.previous_target_weights, {PRODUCT_Q: 1, PRODUCT_C: 0});
  assert.equal(action.target_changed, true);
  assert.deepEqual(action.source_target_weights, {QQQ: .3, BIL: .7});
});

test("mapped product deviation uses aggregated KRW product sleeves", () => {
  const {source, data} = notificationFixture();
  const mapped = {
    strategy: {id: "notification-products-split", name: "Split products", version: 1},
    source: source.strategy.id,
    products: {
      QQQ: {"PRODUCT_Q1.KS": "50%", "PRODUCT_Q2.KS": "50%"},
      BIL: {"PRODUCT_C.KS": "100%"},
    },
  };
  data.QQQ.forEach(row => { row.Open = row.High = row.Low = row.Close = 100; });
  data["PRODUCT_Q1.KS"] = data.QQQ.map((row, index) => ({
    ...row, Open: 100, High: index ? 150 : 100,
    Low: 100, Close: index ? 150 : 100,
  }));
  data["PRODUCT_Q2.KS"] = data.QQQ.map(row => ({...row}));
  data["PRODUCT_C.KS"] = data.BIL.map(row => ({...row}));
  data["KRW=X"] = data.BIL.map(row => ({...row, Open: 1300, High: 1300, Low: 1300, Close: 1300}));
  const definitions = [source, mapped];
  const seedData = Object.fromEntries(Object.entries(data).map(([ticker, rows]) => [ticker, rows.slice(0, 1)]));
  const snapshot = strategySnapshot(definitions, mapped, seedData);
  const result = runStrategyIncremental(definitions, mapped, data, snapshot);
  const context = result.history[0].notificationContext;

  assert.deepEqual(context.source_current_weights, {QQQ: 1, BIL: 0});
  assert.equal(context.target_deviation, 0);
  assert.equal(context.rebalance_required, false);
  assert.ok(context.current_weights["PRODUCT_Q1.KS"] > .59);
  assert.ok(context.current_weights["PRODUCT_Q2.KS"] < .41);
  assert.equal(context.current_weights["PRODUCT_C.KS"], 0);
});

test("notification selection suppresses allocation-neutral churn and rearms deviation warnings", () => {
  const context = overrides => ({
    state_values: {trend_mode: "CAUTION", defense_mode: "NORMAL"},
    state_changes: [], confirmation_started: [], confirmations: [],
    target_deviation: .01, current_weights: {QQQ: 1}, target_weights: {QQQ: 1},
    ...overrides,
  });
  const history = [
    {date: "2025-01-02", notificationContext: context({
      state_changes: [{name: "trend_mode", previous: "BULL", current: "CAUTION"}],
    })},
    {date: "2025-01-03", notificationContext: context({target_deviation: .051, prealerts: [{id: "drift", message: "괴리 경고", matched: true, reset: false}]})},
    {date: "2025-01-06", notificationContext: context({target_deviation: .06, prealerts: [{id: "drift", message: "괴리 경고", matched: true, reset: false}]})},
    {date: "2025-01-07", notificationContext: context({target_deviation: .039, prealerts: [{id: "drift", message: "괴리 경고", matched: false, reset: true}]})},
    {date: "2025-01-08", notificationContext: context({target_deviation: .052, prealerts: [{id: "drift", message: "괴리 경고", matched: true, reset: false}]})},
  ];

  const selected = selectNotificationAlerts(history);

  assert.deepEqual(selected.alerts.map(alert => alert.market_data_at), ["2025-01-03", "2025-01-08"]);
  assert.ok(selected.alerts.every(alert => alert.type === "PREALERT"));
  assert.equal(selected.state.armed_rules.drift, true);
});

test("runtime rotation changes only the configured BIL sleeve", () => {
  const definition = {
    strategy: {id: "rotation", name: "Rotation", version: 1},
    assets: {required: ["QQQ", "TDF", "BIL", "GLD", "SHY", "KOSPI"]},
    target: [{weights: {QQQ: "0%", TDF: "20%", BIL: "80%", GLD: "0%", SHY: "0%", KOSPI: "0%"}}],
    rotation: {
      sleeve: "BIL", check: "monthly", top_n: 2,
      max_single_sleeve_share: "50%", max_gold_sleeve_share: "30%", max_equity_sleeve_share: "30%",
      candidates: [
        {ticker: "GLD", group: "gold", asset_class: "GOLD"},
        {ticker: "SHY", group: "short", asset_class: "BOND"},
        {ticker: "KOSPI", group: "equity", asset_class: "EQUITY"},
      ],
    },
    execution: {days: 1},
  };
  const dates = ["2025-01-02", "2025-01-03"];
  const rows = (values) => dates.map(Date => ({Date, Open: 100, High: 100, Low: 100, Volume: 1, ...values}));
  const data = {
    QQQ: rows({Close: 100}), TDF: rows({Close: 100}),
    BIL: rows({Close: 100, ROC60: 1, ROC120: 2, ROC252: 3}),
    GLD: rows({Close: 110, EMA200: 100, ROC60: 8, ROC120: 11, ROC252: 16, VOL60: .15}),
    SHY: rows({Close: 110, EMA200: 100, ROC60: 5, ROC120: 7, ROC252: 9, VOL60: .05}),
    KOSPI: rows({Close: 90, EMA200: 100, ROC60: 7, ROC120: 9, ROC252: 13, VOL60: .2}),
  };

  const history = runStrategy([definition], definition, data);

  assert.deepEqual(history.at(-1).target, {
    QQQ: 0, TDF: .2, BIL: .2, GLD: .2, SHY: .4, KOSPI: 0,
  });
  assert.match(history.at(-1).reason, /자동 자산 교체/);
});

test("rebalance deviation is checked against the final rotated target", () => {
  const definition = {
    strategy: {id: "rotation-rebalance", name: "Rotation rebalance", version: 1},
    assets: {required: ["TDF", "BIL", "GLD"]},
    target: [{weights: {TDF: "20%", BIL: "80%", GLD: "0%"}}],
    rotation: {
      sleeve: "BIL", check: "monthly", top_n: 1,
      max_single_sleeve_share: "50%", max_gold_sleeve_share: "30%",
      candidates: [{ticker: "GLD", group: "gold", asset_class: "GOLD"}],
    },
    rebalance: [{check: "daily", when: "target_deviation() >= 7.5%"}],
    execution: {days: 1},
  };
  const dates = ["2025-01-02", "2025-01-03", "2025-01-06"];
  const rows = values => dates.map(Date => ({
    Date, Open: 100, High: 100, Low: 100, Volume: 1, ...values,
  }));
  const data = {
    TDF: rows({Close: 100}),
    BIL: rows({Close: 100, ROC60: 1, ROC120: 2, ROC252: 3}),
    GLD: rows({
      Close: 110, EMA200: 100, ROC60: 8, ROC120: 11, ROC252: 16, VOL60: .15,
    }),
  };

  const history = runStrategy([definition], definition, data);

  assert.equal(history.filter(row => row.target).length, 1);
  const target = history.find(row => row.target).target;
  assert.equal(target.TDF, .2);
  assert.ok(Math.abs(target.BIL - .56) < 1e-12);
  assert.equal(target.GLD, .24);
});

test("weight_deviation triggers a directional QQQ underweight rebalance", () => {
  const definition = {
    strategy: {id: "directional-band", name: "Directional band", version: 1},
    assets: {required: ["QQQ", "BIL"], risk: ["QQQ"]},
    target: [{weights: {QQQ: "30%", BIL: "70%"}}],
    rebalance: [{
      check: "daily",
      when: "target_deviation() >= 7.5% or weight_deviation('QQQ') <= -4%",
    }],
    execution: {days: 1},
  };
  const dates = ["2025-01-02", "2025-01-03", "2025-01-06"];
  const rows = closes => dates.map((Date, index) => ({
    Date, Open: index ? closes[index - 1] : closes[index],
    High: closes[index], Low: closes[index], Close: closes[index], Volume: 1,
  }));
  const data = {QQQ: rows([100, 80, 80]), BIL: rows([100, 100, 100])};

  const history = runStrategy([definition], definition, data);

  assert.equal(history.filter(row => row.target).length, 2);
  assert.deepEqual(history.at(-1).target, {QQQ: .3, BIL: .7});
});

test("LOCAL signals calculate deviation with KRW valuation weights", () => {
  const definition = {
    strategy: {id: "mixed-deviation", name: "Mixed deviation", version: 1},
    assets: {required: ["QQQ", "KOSPI"]},
    valuation: {
      currency: "KRW", fx_ticker: "KRW=X",
      foreign_assets: ["QQQ"], signal_currency: "LOCAL",
    },
    target: [{weights: {QQQ: "50%", KOSPI: "50%"}}],
    rebalance: [{check: "daily", when: "target_deviation() >= 7.5%"}],
    execution: {days: 1},
  };
  const dates = ["2025-01-02", "2025-01-03", "2025-01-06"];
  const rows = price => dates.map(Date => ({
    Date, Open: price, High: price, Low: price, Close: price, Volume: 1,
  }));
  const data = {QQQ: rows(100), KOSPI: rows(100), "KRW=X": rows(10)};

  const history = runStrategy([definition], definition, data);

  assert.equal(history.filter(row => row.target).length, 1);
});

test("rotation keeps incumbent assets when the monthly weight change is small", () => {
  const definition = {
    strategy: {id: "stable-rotation", name: "Stable rotation", version: 1},
    assets: {required: ["BIL", "GLD", "SHY"]},
    target: [{weights: {BIL: "100%", GLD: "0%", SHY: "0%"}}],
    rotation: {
      sleeve: "BIL", check: "monthly", top_n: 2,
      max_single_sleeve_share: "100%", max_gold_sleeve_share: "100%",
      minimum_weight_change: "10%", switch_score_margin: "3%",
      candidates: [
        {ticker: "GLD", group: "gold", asset_class: "GOLD"},
        {ticker: "SHY", group: "short", asset_class: "BOND"},
      ],
    },
    execution: {days: 1},
  };
  const dates = ["2025-01-02", "2025-01-03", "2025-02-03", "2025-02-04"];
  const rows = (volatility) => dates.map((Date, index) => ({
    Date, Open: 100, High: 100, Low: 100, Close: 100, Volume: 1,
    EMA200: 90, ROC60: 8, ROC120: 10, ROC252: 12,
    VOL60: index < 2 ? volatility[0] : volatility[1],
  }));
  const data = {
    BIL: rows([.01, .01]).map(row => ({...row, ROC60: 1, ROC120: 2, ROC252: 3})),
    GLD: rows([.10, .11]),
    SHY: rows([.20, .19]),
  };

  const history = runStrategy([definition], definition, data);

  assert.equal(history.filter(row => row.target).length, 1);
});

test("KRW-adjusted strategy assets include their USD source and FX dependency", () => {
  const definition = {
    strategy: {id: "krw", name: "KRW", version: 1},
    assets: {required: ["QQQ_KRW", "069500.KS"]},
    target: [{weights: {QQQ_KRW: "50%", "069500.KS": "50%"}}],
  };

  assert.deepEqual(
    strategyTickers([definition], definition),
    ["QQQ_KRW", "069500.KS", "QQQ", "KRW=X"],
  );
});

test("LOCAL signal currency keeps native trend while portfolio uses KRW valuation", () => {
  const definition = {
    strategy: {id: "mixed", name: "Mixed", version: 1},
    assets: {required: ["QQQ", "BIL"]},
    valuation: {
      currency: "KRW", fx_ticker: "KRW=X",
      foreign_assets: ["QQQ", "BIL"], signal_currency: "LOCAL",
    },
    variables: {positive: "QQQ.close > QQQ.ema200"},
    target: [
      {when: "variables.positive", weights: {QQQ: "100%", BIL: "0%"}},
      {weights: {QQQ: "0%", BIL: "100%"}},
    ],
    execution: {days: 1},
  };
  const data = {
    QQQ: [
      {Date: "2025-01-02", Open: 100, High: 100, Low: 100, Close: 100, EMA200: 90},
      {Date: "2025-01-03", Open: 110, High: 110, Low: 110, Close: 110, EMA200: 90},
    ],
    BIL: [
      {Date: "2025-01-02", Open: 100, High: 100, Low: 100, Close: 100},
      {Date: "2025-01-03", Open: 100, High: 100, Low: 100, Close: 100},
    ],
    "KRW=X": [
      {Date: "2025-01-02", Open: 1, High: 1, Low: 1, Close: 1},
      {Date: "2025-01-03", Open: .5, High: .5, Low: .5, Close: .5},
    ],
  };

  const history = runStrategy([definition], definition, data);

  assert.equal(history.find(row => row.target).target.QQQ, 1);
});
