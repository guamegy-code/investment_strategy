import {readFile} from "node:fs/promises";
import {buildTdf2050Proxy, parseYaml, strategySnapshot, strategyTickers, TDF2050_PROXY_COMPONENT_WEIGHTS} from "../src/strategy-runtime.js";

const values = process.argv.slice(2), args = Object.fromEntries(values.filter(value => value.startsWith("--")).map(value => [value.slice(2), values[values.indexOf(value) + 1]]));
const worker = args.worker || "https://investment-strategy-data-proxy.guamegi-invest.workers.dev";
const manifestUrl = args.manifest || "https://investment-strategy-3zy.pages.dev/strategies/manifest.json";
const strategyId = args["strategy-id"];
const apiKey = process.env.NOTIFICATION_API_KEY;
if (!strategyId || !apiKey) throw Error("Use NOTIFICATION_API_KEY=... and --strategy-id <id>.");

const manifestResponse = await fetch(manifestUrl);
if (!manifestResponse.ok) throw Error("Could not load strategy manifest.");
const manifest = await manifestResponse.json(), entries = Array.isArray(manifest) ? manifest : manifest.strategies;
const definitions = await Promise.all(entries.map(async entry => {
  const path = typeof entry === "string" ? entry : entry.path;
  const response = await fetch(new URL(path, manifestUrl));
  if (!response.ok) throw Error(`Could not load ${path}`);
  return parseYaml(await response.text());
}));
if (args.yaml) definitions.push(parseYaml(await readFile(args.yaml, "utf8")));
const definition = definitions.find(item => item?.strategy?.id === strategyId);
if (!definition) throw Error(`Strategy not found: ${strategyId}`);
const tickers = new Set(strategyTickers(definitions, definition));
if (tickers.has("TDF2050_PROXY")) Object.keys(TDF2050_PROXY_COMPONENT_WEIGHTS).forEach(ticker => tickers.add(ticker));
const data = {};
for (const requested of [...tickers].filter(ticker => ticker !== "TDF2050_PROXY")) {
  const endpoint = new URL("/prices", worker);
  endpoint.search = new URLSearchParams({tickers: requested, start: "2010-01-01"});
  const response = await fetch(endpoint);
  if (!response.ok) throw Error(`Could not fetch ${requested}: ${await response.text()}`);
  const rows = (await response.json()).data[requested];
  data[requested] = rows.map(row => ({Date: row.date, Open: row.open, High: row.high, Low: row.low, Close: row.close, Volume: row.volume}));
}
if (tickers.has("TDF2050_PROXY")) data.TDF2050_PROXY = buildTdf2050Proxy(data);
const snapshot = strategySnapshot(definitions, definition, data);
const response = await fetch(new URL("/notification-seeds", worker), {method: "POST", headers: {Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json"}, body: JSON.stringify({strategy_id: strategyId, snapshot})});
if (!response.ok) throw Error(await response.text());
console.log(`Seeded ${strategyId} at ${snapshot.date}.`);
