import assert from "node:assert/strict";
import test from "node:test";
import {gzipSync} from "node:zlib";

import worker, {createFallbackTickerLoader, loadPriceRange, loadTicker} from "../src/index.js";
import {mapProductTarget, runStrategy, strategyTickers} from "../src/strategy-runtime.js";

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
  assert.deepEqual(writes.map(([key]) => key).sort(), ["prices:QQQ", "prices:SPY"]);
});

test("loadPriceRange backfills history when KV contains only recent rows", async (context) => {
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
    get: async () => JSON.stringify(cached),
    put: async (key, value) => { writes.push([key, JSON.parse(value)]); },
  }};

  const result = await loadPriceRange(
    env,
    null,
    "QQQ",
    Math.floor(Date.parse("2024-01-01") / 1000),
    Math.floor(Date.parse("2026-08-26") / 1000),
    async () => fallback,
  );

  assert.equal(result.stale, true);
  assert.deepEqual(result.rows.map(row => row.date), ["2024-01-02", "2026-08-25"]);
  assert.equal(writes.at(-1)[0], "prices:QQQ");
  assert.deepEqual(writes.at(-1)[1].map(row => row.date), ["2024-01-02", "2026-08-25"]);
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
