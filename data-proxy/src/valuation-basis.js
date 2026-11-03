import {addCompositeValuationScore} from './composite-valuation.js';

export const VALUATION_BASIS_VERSION = 'composite-full-history-v1';
export const VALUATION_HISTORY_START = '2004-01-01';
const FIRST_ROWS = {QQQ:'2004-01-10', SPY:'2004-01-10', BIL:'2007-06-10'};

// An input identity excludes the current, unfinished month. Monthly scores are
// fixed from the previous month's final session, while corrections invalidate
// the identity even when the latest row's date did not change.
export async function valuationInput(data, month, expectedSourceDate = null) {
  const input = {};
  for (const ticker of Object.keys(FIRST_ROWS)) {
    const rows = (data[ticker] || []).filter(row => row.Date >= VALUATION_HISTORY_START && row.Date.slice(0,7) < month);
    if (!rows.length || rows[0].Date > FIRST_ROWS[ticker]) throw new Error(`valuation history incomplete: ${ticker}`);
    if (rows.at(-1).Date.slice(0,7) !== previousMonth(month)) throw new Error(`valuation month incomplete: ${ticker}`);
    input[ticker] = rows.map(row => ({Date:row.Date, Close:Number(row.Close), RawClose:Number(row.RawClose ?? row.Close), Dividends:Number(row.Dividends || 0)}));
  }
  const dates = Object.values(input).map(rows => rows.at(-1).Date);
  if (!dates.every(date => date === dates[0])) throw new Error('valuation month has mismatched market sessions');
  if (expectedSourceDate && dates[0] !== expectedSourceDate) throw new Error(`valuation closing session incomplete: ${expectedSourceDate}`);
  const encoded = new TextEncoder().encode(JSON.stringify([VALUATION_BASIS_VERSION,month,input]));
  const digest = await crypto.subtle.digest('SHA-256',encoded);
  return {month, revision:[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join(''), data:input};
}

function previousMonth(month) {
  const date = new Date(`${month}-01T00:00:00Z`);
  date.setUTCMonth(date.getUTCMonth()-1);
  return date.toISOString().slice(0,7);
}

export function buildValuationTable(input) {
  const data = structuredClone(input.data);
  // A sentinel exposes the previous month's final score without using any
  // current-month prices. It is not persisted as an observed market price.
  data.QQQ.push({...data.QQQ.at(-1),Date:`${input.month}-01`});
  addCompositeValuationScore(data);
  const scores = [];
  for (let index=0;index<data.QQQ.length;index++) {
    const row=data.QQQ[index],month=row.Date.slice(0,7);
    if (Number.isFinite(row.VALUATION_SCORE) && scores.at(-1)?.month !== month) {
      scores.push({month,source_date:data.QQQ[index-1]?.Date,score:row.VALUATION_SCORE});
    }
  }
  if (scores.at(-1)?.month !== input.month) throw new Error('valuation score is not ready');
  return {version:VALUATION_BASIS_VERSION,revision:input.revision,month:input.month,scores};
}

export function applyValuationTable(data, table) {
  if (table?.version !== VALUATION_BASIS_VERSION) throw new Error('unsupported valuation calculation version');
  const scores=new Map(table.scores.map(row=>[row.month,row.score]));
  data.QQQ=(data.QQQ || []).map(row=>({...row,VALUATION_SCORE:scores.get(row.Date.slice(0,7)) ?? NaN}));
  return data;
}

export async function sharedValuationTable(env,data,month,expectedSourceDate=null) {
  const input=await valuationInput(data,month,expectedSourceDate),key=`valuation-table:${VALUATION_BASIS_VERSION}:${month}`;
  const saved=JSON.parse(await env.MARKET_DATA.get(key) || 'null');
  if (saved?.revision === input.revision) return saved;
  const table=env.VALUATION_DATA
    ? await env.VALUATION_DATA.getByName(VALUATION_BASIS_VERSION).build(input)
    : buildValuationTable(input);
  await env.MARKET_DATA.put(key,JSON.stringify(table));
  return table;
}

export async function calculationBasis(table,date) {
  const history=table.scores.filter(row=>row.month<=date.slice(0,7));
  const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(history)));
  return {version:VALUATION_BASIS_VERSION,month:date.slice(0,7),history_revision:[...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,'0')).join('')};
}
