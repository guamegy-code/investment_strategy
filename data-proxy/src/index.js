import {
  TDF2050_PROXY_COMPONENT_WEIGHTS,
  addIndicators,
  buildTdf2050Proxy,
  parseYaml,
  runStrategy,
  runStrategyIncremental,
  selectNotificationAlerts,
  strategySnapshot,
  strategyTickers,
} from "./strategy-runtime.js";
import {addCompositeValuationScore} from "./composite-valuation.js";

const CACHE_SECONDS = 60 * 60 * 12;
const MAX_TICKERS = 20;
const MAX_RANGE_DAYS = 365 * 366;
const MAX_PRIVATE_STRATEGIES = 20;
const MAX_PRIVATE_STRATEGY_BYTES = 100_000;
const NOTIFICATION_TAIL_ROWS = 320;
const NOTIFICATION_LOOKBACK_DAYS = 500;
const RECENT_CACHE_ROWS = 10;
const TICKER_LABEL_CACHE_SECONDS = 60 * 60 * 24 * 30;
const MARKET_REFRESH_RETRY_SECONDS = [5 * 60, 15 * 60, 30 * 60, 2 * 60 * 60];
const COMPOSITE_VALUATION_TICKERS = new Set(["QQQ", "SPY", "BIL"]);
const COMPOSITE_HISTORY_OPTIONS = {lookbackDays: 2200, tailRows: 1500};
const YAHOO_CHART_HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"];
const YAHOO_HEADERS = {
  Accept: "application/json,text/plain,*/*",
  "Accept-Language": "en-US,en;q=0.9",
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
};

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
  "Cache-Control": `public, max-age=${CACHE_SECONDS}`,
  "Content-Type": "application/json; charset=utf-8",
};

const KRW_ADJUSTED_SUFFIX = "_KRW";

function krwAdjustedBaseTicker(ticker) {
  return ticker.endsWith(KRW_ADJUSTED_SUFFIX)
    ? ticker.slice(0, -KRW_ADJUSTED_SUFFIX.length)
    : null;
}

function response(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...CORS_HEADERS,
      ...(status >= 400 ? {"Cache-Control": "no-store"} : {}),
      ...headers,
    },
  });
}

function parseTickers(value) {
  const tickers = [...new Set((value || "").split(",")
    .map((ticker) => ticker.trim().toUpperCase())
    .filter(Boolean))];
  if (!tickers.length || tickers.length > MAX_TICKERS || tickers.some(
    (ticker) => !/^[A-Z0-9_.^=:-]{1,24}$/.test(ticker)
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

export async function loadTicker(ticker, start, end, {bypassCache = false} = {}) {
  const upstreamTicker = ticker === "KRW=X" ? "USDKRW=X" : ticker;
  let upstream = null;
  let lastStatus = null;
  let lastError = null;
  for (const host of YAHOO_CHART_HOSTS) {
    const source = new URL(`https://${host}/v8/finance/chart/${encodeURIComponent(upstreamTicker)}`);
    source.search = new URLSearchParams({
      period1: String(start), period2: String(end), interval: "1d", events: "div,splits",
    });
    try {
      const candidate = await fetch(source, {
        headers: YAHOO_HEADERS,
        ...(bypassCache
          ? {cache: "no-store"}
          : {cf: {cacheTtl: CACHE_SECONDS, cacheEverything: true}}),
      });
      if (candidate.ok) {
        upstream = candidate;
        break;
      }
      lastStatus = candidate.status;
    } catch (error) {
      lastError = error;
    }
  }
  if (!upstream) {
    if (lastStatus !== null) throw new Error(`${ticker}: upstream returned ${lastStatus}`);
    throw new Error(`${ticker}: upstream request failed`, {cause: lastError});
  }
  const payload = await upstream.json();
  const result = payload?.chart?.result?.[0];
  if (!result?.timestamp || !result?.indicators?.quote?.[0]) {
    throw new Error(`${ticker}: no daily price data available`);
  }
  const quote = result.indicators.quote[0];
  const adjusted = result.indicators.adjclose?.[0]?.adjclose;
  const dividends = new Map();
  for (const item of Object.values(result.events?.dividends || {})) {
    const date = new Date(item.date * 1000).toISOString().slice(0, 10);
    dividends.set(date, (dividends.get(date) || 0) + Number(item.amount || 0));
  }
  const rows = result.timestamp.map((timestamp, index) => {
    const rawClose = quote.close[index];
    const close = adjusted?.[index] ?? rawClose;
    const factor = close / rawClose;
    const date = new Date(timestamp * 1000).toISOString().slice(0, 10);
    return {
      date,
      open: quote.open[index] * factor, high: quote.high[index] * factor,
      low: quote.low[index] * factor, close, volume: quote.volume[index],
      raw_close: rawClose, dividends: dividends.get(date) || 0,
    };
  }).filter((row) => Object.values(row).every((value) => Number.isFinite(value) || typeof value === "string"));
  if (!rows.length) throw new Error(`${ticker}: no complete daily rows available`);
  return rows;
}

function mergePriceRows(existing, incoming) {
  const merged = new Map((existing || []).map(row => [row.date, row]));
  for (const row of incoming) merged.set(row.date, row);
  return [...merged.values()].sort((left, right) => left.date.localeCompare(right.date));
}

function buildKrwAdjustedRows(baseRows, fxRows) {
  const fxByDate = new Map(fxRows.map(row => [row.Date, Number(row.Close)]));
  let lastFx = NaN;
  const rows = [];
  for (const row of [...baseRows].sort((left, right) => left.Date.localeCompare(right.Date))) {
    const knownFx = fxByDate.get(row.Date);
    if (Number.isFinite(knownFx)) lastFx = knownFx;
    if (!Number.isFinite(lastFx)) continue;
    rows.push({
      Date: row.Date,
      Open: Number(row.Open) * lastFx,
      High: Number(row.High) * lastFx,
      Low: Number(row.Low) * lastFx,
      Close: Number(row.Close) * lastFx,
      Volume: Number(row.Volume) || 0,
    });
  }
  return addIndicators(rows);
}

function rowsWithinRange(rows, start, end) {
  const startDate = new Date(start * 1000).toISOString().slice(0, 10);
  const endInstant = new Date(end * 1000);
  const endDate = endInstant.toISOString().slice(0, 10);
  const includeEndDate = endInstant.getUTCHours() !== 0
    || endInstant.getUTCMinutes() !== 0
    || endInstant.getUTCSeconds() !== 0;
  return rows.filter((row) => row.date >= startDate
    && (row.date < endDate || includeEndDate && row.date === endDate));
}

function fallbackCsvRows(csv) {
  const [header, ...lines] = String(csv || "").trim().split(/\r?\n/).filter(Boolean);
  if (!header) return [];
  const columns = header.split(",");
  return lines.map((line) => {
    const values = line.split(","), row = {};
    columns.forEach((column, index) => { row[column] = values[index]; });
    return {
      date: row.Date,
      open: Number(row.Open), high: Number(row.High), low: Number(row.Low),
      close: Number(row.Close), volume: Number(row.Volume),
      raw_close: Number(row.RawClose || row.Close), dividends: Number(row.Dividends || 0),
    };
  }).filter((row) => row.date && [row.open, row.high, row.low, row.close, row.volume].every(Number.isFinite));
}

function fallbackTickerSlug(ticker) {
  return btoa(ticker).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

async function loadFallbackTicker(env, ticker) {
  if (!env.MARKET_DATA_FALLBACK_BASE_URL) return [];
  const fallbackUrl = new URL(`${fallbackTickerSlug(ticker)}.csv.gz`, env.MARKET_DATA_FALLBACK_BASE_URL);
  const fallback = await fetch(fallbackUrl, {
    cf: {cacheTtl: CACHE_SECONDS, cacheEverything: true},
  });
  if (fallback.status === 404) return [];
  if (!fallback.ok || !fallback.body) {
    throw new Error(`${ticker}: market data fallback returned ${fallback.status}`);
  }
  const stream = fallback.body.pipeThrough(new DecompressionStream("gzip"));
  return fallbackCsvRows(await new Response(stream).text());
}

function historicalObjectKey(ticker) {
  return `history/${fallbackTickerSlug(ticker)}.csv.gz`;
}

async function loadHistoricalTicker(env, ticker, loadFallbackTicker = null) {
  const object = env.MARKET_HISTORY
    ? await env.MARKET_HISTORY.get(historicalObjectKey(ticker))
    : null;
  if (object?.body) {
    const stream = object.body.pipeThrough(new DecompressionStream("gzip"));
    return fallbackCsvRows(await new Response(stream).text());
  }
  return loadFallbackTicker ? loadFallbackTicker(ticker) : [];
}

export function createFallbackTickerLoader(env) {
  const loads = new Map();
  return (ticker) => {
    if (!loads.has(ticker)) loads.set(ticker, loadFallbackTicker(env, ticker));
    return loads.get(ticker);
  };
}

function samePriceRows(left, right) {
  const comparable = (value) => {
    if (Array.isArray(value)) return value.map(comparable);
    if (!value || typeof value !== "object") return value;
    const row = {...value};
    if (Object.hasOwn(row, "close")) {
      row.raw_close = Number.isFinite(Number(row.raw_close)) ? Number(row.raw_close) : Number(row.close);
      row.dividends = Number.isFinite(Number(row.dividends)) ? Number(row.dividends) : 0;
    }
    if (Object.hasOwn(row, "Close")) {
      row.RawClose = Number.isFinite(Number(row.RawClose)) ? Number(row.RawClose) : Number(row.Close);
      row.Dividends = Number.isFinite(Number(row.Dividends)) ? Number(row.Dividends) : 0;
    }
    return row;
  };
  return JSON.stringify(comparable(left)) === JSON.stringify(comparable(right));
}

async function saveRecentPriceRows(env, ctx, ticker, baseRows, rows) {
  if (!env.MARKET_DATA) return;
  const baseByDate = new Map(baseRows.map(row => [row.date, row]));
  const recent = rows.filter(row => !samePriceRows(baseByDate.get(row.date), row)).slice(-RECENT_CACHE_ROWS);
  const key = `recent-prices:${ticker}`;
  const existing = JSON.parse(await env.MARKET_DATA.get(key) || "null") || [];
  if (samePriceRows(existing, recent)) return;
  const write = env.MARKET_DATA.put(key, JSON.stringify(recent))
    .catch((error) => console.warn(JSON.stringify({
      event: "market_data_cache_write_failed",
      ticker,
      message: error instanceof Error ? error.message : String(error),
    })));
  if (ctx) ctx.waitUntil(write);
  else await write;
}

export async function loadPriceRange(env, ctx, ticker, start, end, loadFallbackTicker = null) {
  const baseTicker = krwAdjustedBaseTicker(ticker);
  if (baseTicker) {
    const [base, fx] = await Promise.all([
      loadPriceRange(env, ctx, baseTicker, start, end, loadFallbackTicker),
      loadPriceRange(env, ctx, "KRW=X", start, end, loadFallbackTicker),
    ]);
    const fxByDate = new Map(fx.rows.map(row => [row.date, row.close]));
    let lastFx = null;
    const rows = base.rows.map(row => {
      const knownFx = fxByDate.get(row.date);
      if (Number.isFinite(knownFx)) lastFx = knownFx;
      return Number.isFinite(lastFx) ? {
        date: row.date,
        open: row.open * lastFx,
        high: row.high * lastFx,
        low: row.low * lastFx,
        close: row.close * lastFx,
        volume: row.volume,
      } : null;
    }).filter(Boolean);
    if (!rows.length) throw new Error(`${ticker}: no overlapping KRW conversion data available`);
    return {rows, stale: base.stale || fx.stale};
  }
  const [baseRows, recentRows] = await Promise.all([
    loadHistoricalTicker(env, ticker, loadFallbackTicker),
    env.MARKET_DATA
      ? env.MARKET_DATA.get(`recent-prices:${ticker}`).then(value => JSON.parse(value || "null"))
      : null,
  ]);
  let normalized = mergePriceRows(baseRows, Array.isArray(recentRows) ? recentRows : []);
  let stale = false;
  const earliest = normalized.reduce((date, row) => !date || row.date < date ? row.date : date, "");
  const latest = normalized.reduce((date, row) => row.date > date ? row.date : date, "");
  const segments = [];
  // Recent KV rows are only a small overlay, not proof that the historical
  // interval between the first and last cached row is complete.
  if (!baseRows.length) segments.push([start, end]);
  else {
    const earliestSecond = Math.floor(Date.parse(earliest) / 1000);
    const latestSecond = Math.floor(Date.parse(latest) / 1000);
    if (start < earliestSecond) segments.push([start, Math.min(end, earliestSecond)]);
    const endDate = new Date(end * 1000).toISOString().slice(0, 10);
    if (latest < endDate) segments.push([Math.max(start, latestSecond), end]);
  }
  let fallbackRows = null;
  for (const [segmentStart, segmentEnd] of segments) {
    if (segmentStart >= segmentEnd) continue;
    try {
      const incoming = await loadTicker(ticker, segmentStart, segmentEnd);
      const merged = mergePriceRows(normalized, incoming);
      if (!samePriceRows(normalized, merged)) {
        normalized = merged;
        await saveRecentPriceRows(env, ctx, ticker, baseRows, normalized);
      }
    } catch (error) {
      if (loadFallbackTicker) {
        fallbackRows ||= await loadFallbackTicker(ticker);
        const merged = mergePriceRows(normalized, fallbackRows);
        if (!samePriceRows(normalized, merged)) {
          normalized = merged;
          await saveRecentPriceRows(env, ctx, ticker, baseRows, normalized);
        }
      }
      if (!rowsWithinRange(normalized, start, end).length) throw error;
      stale = true;
    }
  }
  const rows = rowsWithinRange(normalized, start, end);
  if (!rows.length) throw new Error(`${ticker}: no daily price data available`);
  return {rows, stale};
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

function zonedDateParts(now, timeZone) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(now));
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return {
    date: `${values.year}-${values.month}-${values.day}`,
    minute: Number(values.hour) * 60 + Number(values.minute),
  };
}

function previousWeekday(date, includeDate = false) {
  const value = new Date(`${date}T12:00:00Z`);
  if (!includeDate) value.setUTCDate(value.getUTCDate() - 1);
  while (value.getUTCDay() === 0 || value.getUTCDay() === 6) {
    value.setUTCDate(value.getUTCDate() - 1);
  }
  return value.toISOString().slice(0, 10);
}

function tickerMarket(ticker) {
  return /\.(KS|KQ)$/i.test(ticker) ? "KRX" : "US";
}

export function expectedCompletedSession(ticker, now = Date.now()) {
  const market = tickerMarket(ticker);
  const local = zonedDateParts(now, market === "KRX" ? "Asia/Seoul" : "America/New_York");
  const closeMinute = market === "KRX" ? 16 * 60 + 10 : 16 * 60 + 15;
  return previousWeekday(local.date, local.minute >= closeMinute);
}

function refreshMetadataKey(ticker) {
  return `market-refresh:${ticker}`;
}

function refreshDelaySeconds(metadata, expectedSession, latestSession) {
  const sameAttempt = metadata?.expected_session === expectedSession
    && metadata?.latest_session === latestSession;
  const retryCount = sameAttempt ? Number(metadata.retry_count || 0) + 1 : 0;
  return {
    retryCount,
    delay: MARKET_REFRESH_RETRY_SECONDS[Math.min(retryCount, MARKET_REFRESH_RETRY_SECONDS.length - 1)],
  };
}

export function calculationDefinition(definitions, definition) {
  const byId = new Map(definitions.map(item => [item?.strategy?.id, item]));
  const visited = new Set();
  let current = definition;
  while (current?.source) {
    if (visited.has(current.strategy?.id)) throw new Error("circular strategy source");
    visited.add(current.strategy?.id);
    current = byId.get(current.source);
  }
  return current || definition;
}

export function usesCompositeValuation(definitions, definition) {
  return JSON.stringify(calculationDefinition(definitions, definition)).includes("QQQ.valuation_score");
}

export function historyOptionsForTicker(ticker, hasCompositeValuation) {
  return hasCompositeValuation && COMPOSITE_VALUATION_TICKERS.has(ticker)
    ? COMPOSITE_HISTORY_OPTIONS
    : {};
}

export async function refreshNotificationTicker(env, ticker, {
  lookbackDays = NOTIFICATION_LOOKBACK_DAYS,
  tailRows = NOTIFICATION_TAIL_ROWS,
  now = Date.now(),
} = {}) {
  if (!env.MARKET_DATA) throw new Error("MARKET_DATA KV binding is not configured");
  const key = `notification-tail:${ticker}`;
  const metadataKey = refreshMetadataKey(ticker);
  const [storedRows, storedMetadata] = await Promise.all([
    env.MARKET_DATA.get(key), env.MARKET_DATA.get(metadataKey),
  ]);
  const existing = JSON.parse(storedRows || "[]");
  const metadata = JSON.parse(storedMetadata || "null");
  const latest = existing.reduce((date, row) => row.Date > date ? row.Date : date, "");
  const expectedSession = expectedCompletedSession(ticker, now);
  const requiredStart = Math.floor((now - lookbackDays * 86400000) / 1000);
  const earliest = existing.reduce((date, row) => !date || row.Date < date ? row.Date : date, "");
  const needsHistory = !earliest
    || existing.length < tailRows && Date.parse(earliest) / 1000 > requiredStart;
  const retryAt = Date.parse(metadata?.next_refresh_at || "");
  const sessionSatisfied = latest >= expectedSession;
  if (!needsHistory && metadata?.expected_session === expectedSession
      && (sessionSatisfied || Number.isFinite(retryAt) && now < retryAt)) {
    return {
      rows: existing,
      freshness: {
        market: tickerMarket(ticker), expected_session: expectedSession,
        latest_session: latest, fetched_at: metadata.fetched_at || null,
        next_refresh_at: metadata.next_refresh_at || null,
        status: sessionSatisfied ? "fresh" : "pending",
        upstream_checked: false, rows_changed: false,
      },
    };
  }
  const start = !earliest || Date.parse(earliest) / 1000 > requiredStart
    ? requiredStart
    : latest ? Math.floor(Date.parse(latest) / 1000) : requiredStart;
  let rows = existing, errorMessage = null;
  try {
    const incoming = await loadTicker(ticker, start, Math.floor(now / 1000), {bypassCache: true});
    rows = mergeRows(existing, incoming.map(row => ({Date: row.date, Open: row.open, High: row.high, Low: row.low, Close: row.close, Volume: row.volume, RawClose: row.raw_close, Dividends: row.dividends}))).slice(-tailRows);
  } catch (error) {
    if (!existing.length) throw error;
    errorMessage = error instanceof Error ? error.message : String(error);
  }
  const latestSession = rows.reduce((date, row) => row.Date > date ? row.Date : date, "");
  const rowsChanged = !samePriceRows(existing, rows);
  const satisfied = latestSession >= expectedSession;
  const retry = refreshDelaySeconds(metadata, expectedSession, latestSession);
  const nextRefreshAt = satisfied ? null : new Date(now + retry.delay * 1000).toISOString();
  const nextMetadata = {
    market: tickerMarket(ticker), expected_session: expectedSession,
    latest_session: latestSession, fetched_at: new Date(now).toISOString(),
    next_refresh_at: nextRefreshAt, retry_count: satisfied ? 0 : retry.retryCount,
    status: errorMessage ? "stale" : satisfied ? "fresh" : "pending",
    ...(errorMessage ? {last_error: errorMessage} : {}),
  };
  const writes = [env.MARKET_DATA.put(metadataKey, JSON.stringify(nextMetadata))];
  if (rowsChanged) writes.push(env.MARKET_DATA.put(key, JSON.stringify(rows)));
  await Promise.all(writes);
  return {
    rows,
    freshness: {...nextMetadata, upstream_checked: true, rows_changed: rowsChanged},
  };
}

async function notificationSnapshot(env, id) {
  if (!env.MARKET_DATA) throw new Error("MARKET_DATA KV binding is not configured");
  return JSON.parse(await env.MARKET_DATA.get(`notification-state:${id}`) || "null");
}

async function saveNotificationSnapshot(env, id, snapshot) {
  const key = `notification-state:${id}`;
  const existing = await env.MARKET_DATA.get(key);
  if (existing !== JSON.stringify(snapshot)) await env.MARKET_DATA.put(key, JSON.stringify(snapshot));
}

async function notificationEvaluation(request, env) {
  requireNotificationAuth(request, env);
  const body = await request.json();
  const preview = body.preview === true;
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
  const hasCompositeValuation = selected.some(definition => usesCompositeValuation(definitions, definition));
  const hasTdfProxy = [...tickers].some(ticker => ticker === "TDF2050_PROXY" || krwAdjustedBaseTicker(ticker) === "TDF2050_PROXY");
  if (hasTdfProxy) for (const ticker of Object.keys(TDF2050_PROXY_COMPONENT_WEIGHTS)) tickers.add(ticker);
  const directTickers = [...tickers].filter(ticker => ticker !== "TDF2050_PROXY" && !krwAdjustedBaseTicker(ticker));
  const refreshed = await refreshTickerSet(
    env, directTickers, ticker => historyOptionsForTicker(ticker, hasCompositeValuation),
  );
  const data = refreshed.data;
  if (hasCompositeValuation) addCompositeValuationScore(data);
  if (hasTdfProxy) {
    data.TDF2050_PROXY = buildTdf2050Proxy(data);
    const priorProxyClose = selected.map(definition => snapshots.get(definition.strategy.id)?.prices?.TDF2050_PROXY).find(Number.isFinite);
    if (priorProxyClose && data.TDF2050_PROXY.length) {
      const factor = priorProxyClose / data.TDF2050_PROXY.at(-1).Close;
      data.TDF2050_PROXY = data.TDF2050_PROXY.map(row => Object.fromEntries(Object.entries(row).map(([key, value]) => [key, typeof value === "number" && key !== "Volume" ? value * factor : value])));
    }
  }
  for (const ticker of [...tickers].filter(krwAdjustedBaseTicker)) {
    const baseTicker = krwAdjustedBaseTicker(ticker);
    data[ticker] = buildKrwAdjustedRows(data[baseTicker] || [], data["KRW=X"] || []);
  }
  const tickerLabels = await cachedTickerLabels(env, tickers);
  const lastDates = body.last_evaluated_dates || {}, evaluations = [];
  let decisionAdvanced = false;
  for (const definition of selected) {
    const previousSnapshot = snapshots.get(definition.strategy.id);
    const result = runStrategyIncremental(definitions, definition, data, previousSnapshot);
    if (result.history.length) decisionAdvanced = true;
    const notification = selectNotificationAlerts(result.history, previousSnapshot.notification || {
      deviation_armed: Number(previousSnapshot.notification_context?.target_deviation || 0) >= .05,
      latest_context: previousSnapshot.notification_context || null,
    });
    const latestContext = notification.state.latest_context || result.snapshot.notification_context || previousSnapshot.notification_context || null;
    const nextSnapshot = {...result.snapshot, notification: notification.state, notification_context: latestContext};
    if (!preview) await saveNotificationSnapshot(env, definition.strategy.id, nextSnapshot);
    const history = result.history, marketDataAt = nextSnapshot.date || previousSnapshot.date, prior = String(lastDates[definition.strategy.id] || "");
    const events = history.filter(row => row.target && row.date > prior);
    const event = events.at(-1);
    const alertMetadata = {
      strategy_id: definition.strategy.id,
      strategy_name: definition.strategy.name,
      strategy_version: String(definition.strategy.version || ""),
      product_names: tickerLabels,
    };
    const alerts = notification.alerts.map(alert => ({...alertMetadata, ...alert}));
    const schedule = latestContext?.notification_display?.schedule
      ?? (latestContext?.notification_display?.weekly === false ? "none" : "weekly");
    const summarySchedule = schedule === "none" && preview ? "daily" : schedule;
    const scheduledSummary = latestContext && summarySchedule !== "none" ? {
      ...alertMetadata,
      type: "SUMMARY",
      summary_schedule: summarySchedule,
      schedule_disabled: schedule === "none",
      market_data_at: marketDataAt,
      state_values: latestContext.state_values || {},
      reason_text: "정기 시장 상황 점검",
      current_weights: latestContext.current_weights || {},
      target_weights: latestContext.target_weights || {},
      weight_changes: latestContext.weight_changes || {},
      target_deviation: Number(latestContext.target_deviation || 0),
      variables: latestContext.variables || {},
      confirmations: latestContext.confirmations || [],
      market: latestContext.market || {},
      notification_display: latestContext.notification_display || null,
      mapped_products: Boolean(latestContext.mapped_products),
      source_strategy_id: latestContext.source_strategy_id || null,
      source_current_weights: latestContext.source_current_weights || null,
      source_target_weights: latestContext.source_target_weights || null,
      product_names: tickerLabels,
    } : null;
    evaluations.push({
      ...alertMetadata,
      market_data_at: marketDataAt,
      rebalance_required: Boolean(event),
      target_weights: event?.target || null,
      execution_days: event?.executionDays || null,
      reason: event?.reason || null,
      state: history.at(-1)?.state || "",
      state_values: latestContext?.state_values || {},
      mapped_products: Boolean(latestContext?.mapped_products),
      alerts,
      scheduled_summary: scheduledSummary,
      weekly_summary: schedule === "weekly" ? scheduledSummary : null,
    });
  }
  const refreshSummary = aggregateRefresh(refreshed.freshness);
  return response({
    market_data_updated: refreshSummary.rows_changed,
    ...refreshSummary,
    decision_advanced: decisionAdvanced,
    price_freshness: refreshed.freshness,
    preview,
    evaluations,
  }, 200, {"Cache-Control": "no-store"});
}

async function refreshTickerSet(env, tickers, optionsForTicker = () => ({}), concurrency = 4) {
  const queue = [...tickers], data = {}, freshness = {};
  const workers = Array.from({length: Math.min(concurrency, queue.length)}, async () => {
    while (queue.length) {
      const ticker = queue.shift();
      const refreshed = await refreshNotificationTicker(env, ticker, optionsForTicker(ticker));
      data[ticker] = refreshed.rows;
      freshness[ticker] = refreshed.freshness;
    }
  });
  await Promise.all(workers);
  return {data, freshness};
}

function aggregateRefresh(freshness) {
  const values = Object.values(freshness);
  return {
    upstream_checked: values.some(item => item.upstream_checked),
    rows_changed: values.some(item => item.rows_changed),
    freshness: values.some(item => item.status === "stale")
      ? "stale"
      : values.some(item => item.status === "pending") ? "pending" : "fresh",
  };
}

export async function loadTickerLabel(ticker) {
  const upstreamTicker = ticker === "KRW=X" ? "USDKRW=X" : ticker;
  let lastStatus = null, lastError = null;
  for (const host of YAHOO_CHART_HOSTS) {
    const source = new URL(`https://${host}/v8/finance/chart/${encodeURIComponent(upstreamTicker)}`);
    source.search = new URLSearchParams({range: "5d", interval: "1d"});
    try {
      const upstream = await fetch(source, {
        headers: YAHOO_HEADERS,
        cf: {cacheTtl: TICKER_LABEL_CACHE_SECONDS, cacheEverything: true},
      });
      if (!upstream.ok) { lastStatus = upstream.status; continue; }
      const meta = (await upstream.json())?.chart?.result?.[0]?.meta || {};
      const label = String(meta.shortName || meta.longName || "").trim();
      return label || null;
    } catch (error) { lastError = error; }
  }
  if (lastStatus !== null) throw new Error(`${ticker}: label upstream returned ${lastStatus}`);
  throw new Error(`${ticker}: label upstream request failed`, {cause: lastError});
}

async function cachedTickerLabels(env, tickers) {
  if (!env.MARKET_DATA) return {};
  const labels = await Promise.all([...tickers]
    .filter(ticker => ticker !== "TDF2050_PROXY" && !krwAdjustedBaseTicker(ticker))
    .map(async ticker => {
      const key = `ticker-label:${ticker}`;
      const cached = await env.MARKET_DATA.get(key);
      if (cached) return [ticker, cached];
      try {
        const label = await loadTickerLabel(ticker);
        if (!label) return null;
        await env.MARKET_DATA.put(key, label, {expirationTtl: TICKER_LABEL_CACHE_SECONDS});
        return [ticker, label];
      } catch { return null; }
    }));
  return Object.fromEntries(labels.filter(Boolean));
}

async function notificationSeed(request, env) {
  requireNotificationAuth(request, env);
  const body = await request.json(), id = String(body.strategy_id || "").trim(), snapshot = body.snapshot;
  if (!id || !snapshot?.date || !snapshot?.portfolio || !snapshot?.runtime) throw new Error("strategy_id and complete snapshot are required");
  const context = snapshot.notification_context || null;
  await saveNotificationSnapshot(env, id, {
    ...snapshot,
    notification: snapshot.notification || {
      deviation_armed: Number(context?.target_deviation || 0) >= .05,
      latest_context: context,
    },
  });
  return response({strategy_id: id, seeded_at: snapshot.date});
}

function portfolioPriceTickers(snapshot) {
  const context = snapshot.notification_context || snapshot.notification?.latest_context || {};
  return [...new Set([
    ...Object.keys(snapshot.portfolio?.positions || {}),
    ...Object.keys(context.target_weights || {}),
  ])].filter(ticker => /^[A-Z0-9_.^=:-]{1,24}$/.test(ticker)
    && ticker !== "TDF2050_PROXY" && !krwAdjustedBaseTicker(ticker));
}

export function valuePortfolioSnapshot(snapshot, data) {
  const context = snapshot.notification_context || snapshot.notification?.latest_context || {};
  const positions = snapshot.portfolio?.positions || {};
  const targetWeights = context.target_weights || {};
  const tickers = [...new Set([...Object.keys(positions), ...Object.keys(targetWeights)])];
  const prices = {}, sessions = {};
  for (const ticker of tickers) {
    const latest = data[ticker]?.at(-1);
    const fallback = Number(snapshot.prices?.[ticker]);
    const price = latest ? Number(latest.Close) : fallback;
    if (Math.abs(Number(positions[ticker] || 0)) > 1e-12 && !Number.isFinite(price)) {
      throw new Error(`${ticker}: no valuation price is available`);
    }
    if (Number.isFinite(price)) prices[ticker] = price;
    if (latest?.Date) sessions[ticker] = latest.Date;
  }
  const cash = Number(snapshot.portfolio?.cash || 0);
  const totalValue = cash + Object.entries(positions).reduce(
    (total, [ticker, shares]) => total + Number(shares) * Number(prices[ticker] || 0), 0,
  );
  if (!Number.isFinite(totalValue) || totalValue <= 0) throw new Error("portfolio value is not positive");
  const currentWeights = Object.fromEntries(tickers.map(ticker => [
    ticker, Number(positions[ticker] || 0) * Number(prices[ticker] || 0) / totalValue,
  ]));
  const targetDeviation = Math.max(...Object.entries(targetWeights).map(
    ([ticker, weight]) => Math.abs(Number(currentWeights[ticker] || 0) - Number(weight)),
  ), 0);
  const valuationDates = Object.values(sessions);
  return {
    signal_as_of: snapshot.date,
    valuation_as_of: valuationDates.length ? valuationDates.sort()[0] : snapshot.date,
    value: totalValue,
    cash_weight: cash / totalValue,
    current_weights: currentWeights,
    target_weights: {...targetWeights},
    target_deviation: targetDeviation,
    prices,
    price_sessions: sessions,
  };
}

async function portfolioValuations(request, env) {
  requireNotificationAuth(request, env);
  const body = await request.json();
  const preview = body.preview === true;
  const ids = [...new Set(Array.isArray(body.strategy_ids) ? body.strategy_ids.map(String) : [])];
  if (!ids.length || ids.length > 20) throw new Error("strategy_ids must contain 1-20 values");
  const publicDefinitions = await loadDefinitions(env);
  const definitions = [...publicDefinitions, ...privateDefinitions(body, publicDefinitions)];
  const byId = new Map(definitions.map(definition => [definition?.strategy?.id, definition]));
  const selected = ids.map(id => byId.get(id)).filter(Boolean);
  if (selected.length !== ids.length) throw new Error("one or more strategy IDs are unknown");
  const snapshots = new Map(await Promise.all(selected.map(async definition => [
    definition.strategy.id, await notificationSnapshot(env, definition.strategy.id),
  ])));
  const unseeded = selected.filter(definition => !snapshots.get(definition.strategy.id)).map(definition => definition.strategy.id);
  if (unseeded.length) throw new Error(`notification seed is required: ${unseeded.join(", ")}`);
  const tickers = new Set(selected.flatMap(definition => portfolioPriceTickers(snapshots.get(definition.strategy.id))));
  const refreshed = await refreshTickerSet(env, tickers, () => ({lookbackDays: 30, tailRows: 20}));
  const labels = await cachedTickerLabels(env, tickers);
  const valuations = [];
  for (const definition of selected) {
    const snapshot = snapshots.get(definition.strategy.id);
    const valuation = {
      strategy_id: definition.strategy.id,
      strategy_name: definition.strategy.name,
      strategy_version: String(definition.strategy.version || ""),
      product_names: labels,
      ...valuePortfolioSnapshot(snapshot, refreshed.data),
    };
    if (!preview) await env.MARKET_DATA.put(
      `portfolio-valuation:${definition.strategy.id}`,
      JSON.stringify({...valuation, valued_at: new Date().toISOString()}),
    );
    valuations.push(valuation);
  }
  return response({
    ...aggregateRefresh(refreshed.freshness),
    decision_advanced: false,
    price_freshness: refreshed.freshness,
    preview,
    valuations,
  }, 200, {"Cache-Control": "no-store"});
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
  const hasCompositeValuation = selected.some(definition => usesCompositeValuation(definitions, definition));
  const hasTdfProxy = [...tickers].some(ticker => ticker === "TDF2050_PROXY" || krwAdjustedBaseTicker(ticker) === "TDF2050_PROXY");
  if (hasTdfProxy) for (const ticker of Object.keys(TDF2050_PROXY_COMPONENT_WEIGHTS)) tickers.add(ticker);
  const directTickers = [...tickers].filter(ticker => ticker !== "TDF2050_PROXY" && !krwAdjustedBaseTicker(ticker));
  const refreshed = await refreshTickerSet(
    env, directTickers, ticker => historyOptionsForTicker(ticker, hasCompositeValuation),
  );
  const data = refreshed.data;
  if (hasCompositeValuation) addCompositeValuationScore(data);
  if (hasTdfProxy) data.TDF2050_PROXY = buildTdf2050Proxy(data);
  for (const ticker of [...tickers].filter(krwAdjustedBaseTicker)) {
    const baseTicker = krwAdjustedBaseTicker(ticker);
    data[ticker] = buildKrwAdjustedRows(data[baseTicker] || [], data["KRW=X"] || []);
  }
  const seeded = [];
  for (const definition of selected) {
    const baseSnapshot = strategySnapshot(definitions, definition, data);
    const context = baseSnapshot.notification_context || null;
    const snapshot = {
      ...baseSnapshot,
      notification: {
        deviation_armed: Number(context?.target_deviation || 0) >= .05,
        latest_context: context,
      },
    };
    await saveNotificationSnapshot(env, definition.strategy.id, snapshot);
    seeded.push({strategy_id: definition.strategy.id, seeded_at: snapshot.date});
  }
  return response({seeded});
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return new Response(null, {headers: CORS_HEADERS});
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/notification-evaluations") {
      try { return await notificationEvaluation(request, env); }
      catch (error) { return response({error: error instanceof Error ? error.message : "notification evaluation failed"}, error?.message === "unauthorized" ? 401 : 400); }
    }
    if (request.method === "POST" && url.pathname === "/portfolio-valuations") {
      try { return await portfolioValuations(request, env); }
      catch (error) { return response({error: error instanceof Error ? error.message : "portfolio valuation failed"}, error?.message === "unauthorized" ? 401 : 400); }
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
    const cacheUrl = new URL(url);
    cacheUrl.searchParams.delete("runtime");
    const cacheTickers = cacheUrl.searchParams.get("tickers");
    if (cacheTickers) cacheUrl.searchParams.set("tickers", cacheTickers.split(",").sort().join(","));
    if (cacheTickers) {
      const sessions = cacheTickers.split(",").map(ticker => ticker.trim().toUpperCase()).filter(Boolean).sort().map(
        ticker => `${ticker}:${expectedCompletedSession(ticker)}`,
      );
      cacheUrl.searchParams.set("_session", sessions.join(","));
    }
    const cacheRequest = new Request(cacheUrl, request);
    const edgeCache = globalThis.caches?.default;
    const cached = edgeCache ? await edgeCache.match(cacheRequest) : null;
    if (cached) {
      const headers = new Headers(cached.headers);
      headers.set("Server-Timing", 'edge-cache;desc="HIT"');
      return new Response(cached.body, {status: cached.status, headers});
    }
    try {
      const tickers = parseTickers(url.searchParams.get("tickers"));
      const end = unixSeconds(url.searchParams.get("end"), Date.now());
      const start = unixSeconds(url.searchParams.get("start"), Date.UTC(2010, 0, 1));
      if (start >= end || end - start > MAX_RANGE_DAYS * 86400) {
        throw new Error(`date range must be positive and no longer than ${MAX_RANGE_DAYS} days`);
      }
      const loadFallbackTicker = createFallbackTickerLoader(env);
      const loaded = await Promise.all(tickers.map(async (ticker) => [
        ticker,
        await loadPriceRange(env, ctx, ticker, start, end, loadFallbackTicker),
      ]));
      const staleTickers = loaded.filter(([, result]) => result.stale).map(([ticker]) => ticker);
      const entries = loaded.map(([ticker, result]) => [ticker, result.rows]);
      const priceFreshness = Object.fromEntries(loaded.map(([ticker, result]) => {
        const expectedSession = expectedCompletedSession(ticker);
        const latestSession = result.rows.at(-1)?.date || null;
        return [ticker, {
          market: tickerMarket(ticker), expected_session: expectedSession,
          latest_session: latestSession,
          status: result.stale ? "stale" : latestSession >= expectedSession ? "fresh" : "pending",
        }];
      }));
      const pending = Object.values(priceFreshness).some(item => item.status !== "fresh");
      const includeLabels = url.searchParams.get("include_labels") === "1";
      const labels = includeLabels ? await cachedTickerLabels(env, tickers) : {};
      const payload = response({
        provider: "yahoo-finance",
        fetched_at: new Date().toISOString(),
        stale_tickers: staleTickers,
        price_freshness: priceFreshness,
        data: Object.fromEntries(entries),
        ...(includeLabels ? {labels} : {}),
      }, 200, {"Cache-Control": `public, max-age=${pending ? 300 : CACHE_SECONDS}`});
      payload.headers.set("Server-Timing", 'edge-cache;desc="MISS"');
      if (edgeCache && ctx) ctx.waitUntil(edgeCache.put(cacheRequest, payload.clone()));
      return payload;
    } catch (error) {
      return response({error: error instanceof Error ? error.message : "data request failed"}, 400);
    }
  },
};
