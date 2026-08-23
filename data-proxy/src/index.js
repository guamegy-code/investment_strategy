import {
  TDF2050_PROXY_COMPONENT_WEIGHTS,
  addIndicators,
  buildTdf2050Proxy,
  parseYaml,
  runStrategy,
  runStrategyIncremental,
  strategySnapshot,
  strategyTickers,
} from "./strategy-runtime.js";

const CACHE_SECONDS = 60 * 60 * 12;
const MAX_TICKERS = 20;
const MAX_RANGE_DAYS = 365 * 366;
const MAX_PRIVATE_STRATEGIES = 20;
const MAX_PRIVATE_STRATEGY_BYTES = 100_000;
const NOTIFICATION_TAIL_ROWS = 320;
const NOTIFICATION_LOOKBACK_DAYS = 500;

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
  "Cache-Control": `public, max-age=${CACHE_SECONDS}`,
  "Content-Type": "application/json; charset=utf-8",
};

function response(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {...CORS_HEADERS, ...headers},
  });
}

function parseTickers(value) {
  const tickers = [...new Set((value || "").split(",")
    .map((ticker) => ticker.trim().toUpperCase())
    .filter(Boolean))];
  if (!tickers.length || tickers.length > MAX_TICKERS || tickers.some(
    (ticker) => !/^[A-Z0-9.^=:-]{1,20}$/.test(ticker)
  )) {
    throw new Error(`tickers must contain 1-${MAX_TICKERS} valid ticker symbols`);
  }
  return tickers;
}

function unixSeconds(value, fallback) {
  const parsed = value ? Date.parse(value) : fallback;
  if (Number.isNaN(parsed)) throw new Error("start and end must be ISO dates");
  return Math.floor(parsed / 1000);
}

async function loadTicker(ticker, start, end) {
  const source = new URL(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}`);
  source.search = new URLSearchParams({
    period1: String(start), period2: String(end), interval: "1d", events: "history",
  });
  const upstream = await fetch(source, {
    headers: {"User-Agent": "investment-strategy-data-proxy/1.0"},
    cf: {cacheTtl: CACHE_SECONDS, cacheEverything: true},
  });
  if (!upstream.ok) throw new Error(`${ticker}: upstream returned ${upstream.status}`);
  const payload = await upstream.json();
  const result = payload?.chart?.result?.[0];
  if (!result?.timestamp || !result?.indicators?.quote?.[0]) {
    throw new Error(`${ticker}: no daily price data available`);
  }
  const quote = result.indicators.quote[0];
  const adjusted = result.indicators.adjclose?.[0]?.adjclose;
  const rows = result.timestamp.map((timestamp, index) => {
    const rawClose = quote.close[index];
    const close = adjusted?.[index] ?? rawClose;
    const factor = close / rawClose;
    return {
      date: new Date(timestamp * 1000).toISOString().slice(0, 10),
      open: quote.open[index] * factor, high: quote.high[index] * factor,
      low: quote.low[index] * factor, close, volume: quote.volume[index],
    };
  }).filter((row) => Object.values(row).every((value) => Number.isFinite(value) || typeof value === "string"));
  if (!rows.length) throw new Error(`${ticker}: no complete daily rows available`);
  return rows;
}

function requireNotificationAuth(request, env) {
  if (!env.NOTIFICATION_API_KEY) throw new Error("notification API is not configured");
  if (request.headers.get("Authorization") !== `Bearer ${env.NOTIFICATION_API_KEY}`) {
    throw new Error("unauthorized");
  }
}

async function loadDefinitions(env) {
  if (!env.STRATEGY_MANIFEST_URL) throw new Error("strategy manifest is not configured");
  const manifestUrl = new URL(env.STRATEGY_MANIFEST_URL);
  const manifestResponse = await fetch(manifestUrl, {cf: {cacheTtl: 300, cacheEverything: true}});
  if (!manifestResponse.ok) throw new Error("strategy manifest could not be loaded");
  const manifest = await manifestResponse.json();
  const entries = Array.isArray(manifest) ? manifest : manifest.strategies;
  if (!Array.isArray(entries)) throw new Error("strategy manifest is invalid");
  return Promise.all(entries.map(async entry => {
    const path = typeof entry === "string" ? entry : entry.path;
    const response = await fetch(new URL(path, manifestUrl), {cf: {cacheTtl: 300, cacheEverything: true}});
    if (!response.ok) throw new Error(`strategy could not be loaded: ${path}`);
    return parseYaml(await response.text());
  }));
}

function privateDefinitions(body, publicDefinitions) {
  const entries = body.private_strategies ?? [];
  if (!Array.isArray(entries) || entries.length > MAX_PRIVATE_STRATEGIES) {
    throw new Error(`private_strategies must contain 0-${MAX_PRIVATE_STRATEGIES} values`);
  }
  const publicIds = new Set(publicDefinitions.map(definition => definition?.strategy?.id));
  const ids = new Set();
  return entries.map(entry => {
    const id = String(entry?.strategy_id || "").trim();
    const yaml = typeof entry?.yaml === "string" ? entry.yaml.trim() : "";
    if (!id || !yaml || yaml.length > MAX_PRIVATE_STRATEGY_BYTES || ids.has(id)) {
      throw new Error("private strategy ID or YAML is invalid");
    }
    if (publicIds.has(id)) throw new Error(`private strategy ID duplicates a public strategy: ${id}`);
    ids.add(id);
    const definition = parseYaml(yaml);
    if (definition?.strategy?.id !== id) {
      throw new Error(`private strategy ID does not match YAML: ${id}`);
    }
    return definition;
  });
}

function mergeRows(existing, incoming) {
  const merged = new Map((existing || []).map(row => [row.Date, row]));
  for (const row of incoming) merged.set(row.Date, row);
  return addIndicators([...merged.values()].sort((left, right) => left.Date.localeCompare(right.Date)));
}

async function cachedTicker(env, ticker) {
  if (!env.MARKET_DATA) throw new Error("MARKET_DATA KV binding is not configured");
  return JSON.parse(await env.MARKET_DATA.get(`ticker:${ticker}`) || "null");
}

async function refreshTicker(env, ticker) {
  const existing = await cachedTicker(env, ticker);
  const latest = (existing || []).reduce((date, row) => row.Date > date ? row.Date : date, "");
  const rows = await loadTicker(ticker, latest ? Math.floor(Date.parse(latest) / 1000) : Date.UTC(2010, 0, 1) / 1000, Math.floor(Date.now() / 1000));
  const normalized = mergeRows(existing, rows.map(row => ({Date: row.date, Open: row.open, High: row.high, Low: row.low, Close: row.close, Volume: row.volume})));
  await env.MARKET_DATA.put(`ticker:${ticker}`, JSON.stringify(normalized));
  return normalized;
}

async function refreshNotificationTicker(env, ticker) {
  if (!env.MARKET_DATA) throw new Error("MARKET_DATA KV binding is not configured");
  const key = `notification-tail:${ticker}`;
  const existing = JSON.parse(await env.MARKET_DATA.get(key) || "[]");
  const latest = existing.reduce((date, row) => row.Date > date ? row.Date : date, "");
  const start = latest ? Math.floor(Date.parse(latest) / 1000) : Math.floor((Date.now() - NOTIFICATION_LOOKBACK_DAYS * 86400000) / 1000);
  const incoming = await loadTicker(ticker, start, Math.floor(Date.now() / 1000));
  const rows = mergeRows(existing, incoming.map(row => ({Date: row.date, Open: row.open, High: row.high, Low: row.low, Close: row.close, Volume: row.volume}))).slice(-NOTIFICATION_TAIL_ROWS);
  await env.MARKET_DATA.put(key, JSON.stringify(rows));
  return rows;
}

async function notificationSnapshot(env, id) {
  if (!env.MARKET_DATA) throw new Error("MARKET_DATA KV binding is not configured");
  return JSON.parse(await env.MARKET_DATA.get(`notification-state:${id}`) || "null");
}

async function saveNotificationSnapshot(env, id, snapshot) {
  await env.MARKET_DATA.put(`notification-state:${id}`, JSON.stringify(snapshot));
}

async function notificationEvaluation(request, env) {
  requireNotificationAuth(request, env);
  const body = await request.json();
  const ids = [...new Set(Array.isArray(body.strategy_ids) ? body.strategy_ids.map(String) : [])];
  if (!ids.length || ids.length > 20) throw new Error("strategy_ids must contain 1-20 values");
  const publicDefinitions = await loadDefinitions(env);
  const definitions = [...publicDefinitions, ...privateDefinitions(body, publicDefinitions)];
  const byId = new Map(definitions.map(definition => [definition?.strategy?.id, definition]));
  const selected = ids.map(id => byId.get(id)).filter(Boolean);
  if (selected.length !== ids.length) throw new Error("one or more strategy IDs are unknown");
  const snapshots = new Map(await Promise.all(selected.map(async definition => [definition.strategy.id, await notificationSnapshot(env, definition.strategy.id)])));
  const unseeded = selected.filter(definition => !snapshots.get(definition.strategy.id)).map(definition => definition.strategy.id);
  if (unseeded.length) throw new Error(`notification seed is required: ${unseeded.join(", ")}`);
  const tickers = new Set(selected.flatMap(definition => strategyTickers(definitions, definition)));
  if (tickers.has("TDF2050_PROXY")) for (const ticker of Object.keys(TDF2050_PROXY_COMPONENT_WEIGHTS)) tickers.add(ticker);
  const data = {};
  for (const ticker of tickers) if (ticker !== "TDF2050_PROXY") data[ticker] = await refreshNotificationTicker(env, ticker);
  if (tickers.has("TDF2050_PROXY")) {
    data.TDF2050_PROXY = buildTdf2050Proxy(data);
    const priorProxyClose = selected.map(definition => snapshots.get(definition.strategy.id)?.prices?.TDF2050_PROXY).find(Number.isFinite);
    if (priorProxyClose && data.TDF2050_PROXY.length) {
      const factor = priorProxyClose / data.TDF2050_PROXY.at(-1).Close;
      data.TDF2050_PROXY = data.TDF2050_PROXY.map(row => Object.fromEntries(Object.entries(row).map(([key, value]) => [key, typeof value === "number" && key !== "Volume" ? value * factor : value])));
    }
  }
  const lastDates = body.last_evaluated_dates || {}, evaluations = [];
  for (const definition of selected) {
    const result = runStrategyIncremental(definitions, definition, data, snapshots.get(definition.strategy.id));
    if (result.snapshot !== snapshots.get(definition.strategy.id)) await saveNotificationSnapshot(env, definition.strategy.id, result.snapshot);
    const history = result.history, marketDataAt = result.snapshot.date || snapshots.get(definition.strategy.id).date, prior = String(lastDates[definition.strategy.id] || "");
    const events = history.filter(row => row.target && row.date > prior);
    const event = events.at(-1);
    evaluations.push({
      strategy_id: definition.strategy.id,
      strategy_name: definition.strategy.name,
      strategy_version: String(definition.strategy.version || ""),
      market_data_at: marketDataAt,
      rebalance_required: Boolean(event),
      target_weights: event?.target || null,
      execution_days: event?.executionDays || null,
      reason: event?.reason || null,
      state: history.at(-1)?.state || "",
    });
  }
  return response({market_data_updated: true, evaluations});
}

async function notificationSeed(request, env) {
  requireNotificationAuth(request, env);
  const body = await request.json(), id = String(body.strategy_id || "").trim(), snapshot = body.snapshot;
  if (!id || !snapshot?.date || !snapshot?.portfolio || !snapshot?.runtime) throw new Error("strategy_id and complete snapshot are required");
  await saveNotificationSnapshot(env, id, snapshot);
  return response({strategy_id: id, seeded_at: snapshot.date});
}

async function notificationBootstrap(request, env) {
  requireNotificationAuth(request, env);
  const body = await request.json();
  const ids = [...new Set(Array.isArray(body.strategy_ids) ? body.strategy_ids.map(String) : [])];
  if (!ids.length || ids.length > 20) throw new Error("strategy_ids must contain 1-20 values");
  const publicDefinitions = await loadDefinitions(env);
  const definitions = [...publicDefinitions, ...privateDefinitions(body, publicDefinitions)];
  const byId = new Map(definitions.map(definition => [definition?.strategy?.id, definition]));
  const selected = ids.map(id => byId.get(id)).filter(Boolean);
  if (selected.length !== ids.length) throw new Error("one or more strategy IDs are unknown");
  const tickers = new Set(selected.flatMap(definition => strategyTickers(definitions, definition)));
  if (tickers.has("TDF2050_PROXY")) for (const ticker of Object.keys(TDF2050_PROXY_COMPONENT_WEIGHTS)) tickers.add(ticker);
  const data = {};
  for (const ticker of tickers) if (ticker !== "TDF2050_PROXY") data[ticker] = await refreshNotificationTicker(env, ticker);
  if (tickers.has("TDF2050_PROXY")) data.TDF2050_PROXY = buildTdf2050Proxy(data);
  const seeded = [];
  for (const definition of selected) {
    const snapshot = strategySnapshot(definitions, definition, data);
    await saveNotificationSnapshot(env, definition.strategy.id, snapshot);
    seeded.push({strategy_id: definition.strategy.id, seeded_at: snapshot.date});
  }
  return response({seeded});
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, {headers: CORS_HEADERS});
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/notification-evaluations") {
      try { return await notificationEvaluation(request, env); }
      catch (error) { return response({error: error instanceof Error ? error.message : "notification evaluation failed"}, error?.message === "unauthorized" ? 401 : 400); }
    }
    if (request.method === "POST" && url.pathname === "/notification-seeds") {
      try { return await notificationSeed(request, env); }
      catch (error) { return response({error: error instanceof Error ? error.message : "notification seed failed"}, error?.message === "unauthorized" ? 401 : 400); }
    }
    if (request.method === "POST" && url.pathname === "/notification-bootstrap") {
      try { return await notificationBootstrap(request, env); }
      catch (error) { return response({error: error instanceof Error ? error.message : "notification bootstrap failed"}, error?.message === "unauthorized" ? 401 : 400); }
    }
    if (request.method !== "GET" || url.pathname !== "/prices") {
      return response({error: "Use GET /prices?tickers=QQQ,BND&start=2010-01-01"}, 404);
    }
    try {
      const tickers = parseTickers(url.searchParams.get("tickers"));
      const end = unixSeconds(url.searchParams.get("end"), Date.now());
      const start = unixSeconds(url.searchParams.get("start"), Date.UTC(2010, 0, 1));
      if (start >= end || end - start > MAX_RANGE_DAYS * 86400) {
        throw new Error(`date range must be positive and no longer than ${MAX_RANGE_DAYS} days`);
      }
      const entries = await Promise.all(tickers.map(async (ticker) => [ticker, await loadTicker(ticker, start, end)]));
      return response({provider: "yahoo-finance", fetched_at: new Date().toISOString(), data: Object.fromEntries(entries)});
    } catch (error) {
      return response({error: error instanceof Error ? error.message : "data request failed"}, 400);
    }
  },
};
